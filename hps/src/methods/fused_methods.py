from functools import partial
import logging
from typing import List, Tuple

import jax.numpy as jnp
import jax

from hps.src.solver_obj import SolverObj, create_solver_obj_2D

from hps.src.methods.local_solve_stage import (
    _local_solve_stage_2D,
    _local_solutions_2D_DtN_uniform,
    _local_solve_stage_2D_ItI,
    _local_solve_stage_2D_chunked,
    _local_solve_stage_3D,
    _local_solve_stage_3D_chunked,
)
from hps.src.methods.uniform_build_stage import (
    _uniform_build_stage_2D_DtN,
    _uniform_build_stage_2D_ItI,
)
from hps.src.methods.uniform_down_pass import (
    vmapped_propogate_down_quad,
    vmapped_propogate_down_quad_ItI,
)
from hps.src.config import (
    DEVICE_ARR,
    get_fused_chunksize_2D,
    HOST_DEVICE,
)


def _fused_local_solve_and_build_2D(
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    P: jax.Array,
    Q_D: jax.Array,
    sidelens: jax.Array,
    l: int,
    p: int,
    source_term: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    bdry_data: jax.Array | None = None,
    host_device: jax.Device = HOST_DEVICE,
) -> None:

    # Get the fused chunksize
    n_leaves = source_term.shape[0]
    chunksize, n_levels_fused = get_fused_chunksize_2D(p, jnp.float64, n_leaves)
    n_chunks = n_leaves // chunksize
    logging.debug(
        "_fused_local_solve_and_build_2D: n_leaves = %i, chunksize = %i, n_chunks = %i, n_levels_fused = %i",
        n_leaves,
        chunksize,
        n_chunks,
        n_levels_fused,
    )
    DtN_arr_lst = []
    v_prime_arr_lst = []
    soln_lst = []
    get_all_operators = bdry_data is not None
    # print("_fused_local_solve_and_build_2D: get_all_operators = ", get_all_operators)
    if get_all_operators:
        # Figure out chunksize for bdry_data
        bdry_data_chunksize = bdry_data.shape[0] // n_chunks

        # These are options that will be passed to build stage.
        bool_subtree_recomp = False
        lowlevel_merge_host_device = DEVICE_ARR[0]
    else:
        # These are options that will be passed to build stage.
        bool_subtree_recomp = True
        lowlevel_merge_host_device = DEVICE_ARR[0]

    # Loop over chunks
    for i in range(0, n_chunks):
        chunk_start_idx = i * chunksize
        chunk_end_idx = min((i + 1) * chunksize, n_leaves)
        # Split the input data into a chunk
        source_term_chunk = source_term[chunk_start_idx:chunk_end_idx]
        sidelens_chunk = sidelens[chunk_start_idx:chunk_end_idx]

        D_xx_coeffs_chunk = (
            D_xx_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xx_coeffs is not None
            else None
        )
        D_xy_coeffs_chunk = (
            D_xy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xy_coeffs is not None
            else None
        )
        D_yy_coeffs_chunk = (
            D_yy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_yy_coeffs is not None
            else None
        )
        D_x_coeffs_chunk = (
            D_x_coeffs[chunk_start_idx:chunk_end_idx]
            if D_x_coeffs is not None
            else None
        )
        D_y_coeffs_chunk = (
            D_y_coeffs[chunk_start_idx:chunk_end_idx]
            if D_y_coeffs is not None
            else None
        )
        I_coeffs_chunk = (
            I_coeffs[chunk_start_idx:chunk_end_idx] if I_coeffs is not None else None
        )
        Y_arr_chunk, DtN_arr_chunk, v_chunk, v_prime_chunk = _local_solve_stage_2D(
            D_xx=D_xx,
            D_xy=D_xy,
            D_yy=D_yy,
            D_x=D_x,
            D_y=D_y,
            P=P,
            sidelens=sidelens_chunk,
            p=p,
            source_term=source_term_chunk,
            D_xx_coeffs=D_xx_coeffs_chunk,
            D_xy_coeffs=D_xy_coeffs_chunk,
            D_yy_coeffs=D_yy_coeffs_chunk,
            D_x_coeffs=D_x_coeffs_chunk,
            D_y_coeffs=D_y_coeffs_chunk,
            I_coeffs=I_coeffs_chunk,
            host_device=DEVICE_ARR[0],
            uniform_grid=True,
            Q_D=Q_D,
        )

        # Merge the chunk as far as we can
        build_stage_out = _uniform_build_stage_2D_DtN(
            DtN_arr_chunk,
            v_prime_chunk,
            n_levels_fused,
            host_device=lowlevel_merge_host_device,
            subtree_recomp=bool_subtree_recomp,
        )

        # Either compute the solution or append the data
        # to the output lists
        if get_all_operators:
            S_lst, g_tilde_lst = build_stage_out
            
            # In this branch, we need Y_arr_chunk, S_lst, v_int_lst, and v_chunk
            # Safe to delete DtN_arr_chunk, v_prime_chunk, and DtN_arr_last
            DtN_arr_chunk.delete()
            v_prime_chunk.delete()

            # Do the down pass
            # idxes_i = jnp.arange(n_bdry_data) % n_chunks == i
            # bdry_data_i = bdry_data[idxes_i]
            bdry_data_i = bdry_data[
                i * bdry_data_chunksize : (i + 1) * bdry_data_chunksize
            ]
            bdry_data_i = jax.device_put(bdry_data_i, DEVICE_ARR[0])

            bdry_data_o = _partial_down_pass(
                bdry_data_i,
                S_lst,
                g_tilde_lst,
                device=DEVICE_ARR[0],
                host_device=DEVICE_ARR[0],
            )
            # compute interior solutions with Y matrices
            logging.debug(
                "_fused_local_solve_and_build_2D: Y_arr_chunk.devices(): %s, bdry_data_o.devices(): %s",
                Y_arr_chunk.devices(),
                bdry_data_o.devices(),
            )
            h_soln_chunk = jnp.einsum("ijk,ik->ij", Y_arr_chunk, bdry_data_o)
            soln_chunk = h_soln_chunk + v_chunk
            # print(
            #     "_fused_local_solve_and_build_2D: soln_chunk.shape = ", soln_chunk.shape
            # )
            # Append the solution to the output list
            soln_lst.append(jax.device_put(soln_chunk, host_device))
        else:
            T_last, h_last = build_stage_out
            # In this branch, we need DtN_arr_last and v_prime_arr_last.
            # Can delete Y_arr_chunk, S_lst, v_int_lst, and v_chunk
            Y_arr_chunk.delete()
            v_chunk.delete()

            # Append only DtN and v_prime vectors to the output lists.
            DtN_arr_lst.append(T_last)
            v_prime_arr_lst.append(h_last)

    if get_all_operators:
        # Compute the down pass

        soln = jnp.concatenate(soln_lst, axis=0)
        return soln
    else:
        # Compute the rest of the merge stages
        DtN_arr = jnp.concatenate(DtN_arr_lst, axis=0)
        for DtN in DtN_arr_lst:
            DtN.delete()
        v_prime_arr = jnp.concatenate(v_prime_arr_lst, axis=0)
        logging.debug("_fused_local_solve_and_build_2D: v_prime_arr shape: %s", v_prime_arr.shape)
        for v_prime in v_prime_arr_lst:
            v_prime.delete()
        logging.debug("_fused_local_solve_and_build_2D: starting final merge")
        # Final call to build_stage to get top-level information
        S_arr_lst, g_tilde_lst = _uniform_build_stage_2D_DtN(
            DtN_arr, v_prime_arr, l - n_levels_fused , host_device=DEVICE_ARR[0], subtree_recomp=False, return_DtN=False
        )
        return S_arr_lst, g_tilde_lst


def _fused_local_solve_and_build_2D_ItI(
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    I_P_0: jax.Array,
    Q_I: jax.Array,
    F: jax.Array,
    G: jax.Array,
    p: int,
    l: int,
    source_term: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    bdry_data: jax.Array | None = None,
    device: jax.Device = DEVICE_ARR[0],
    host_device: jax.Device = HOST_DEVICE,
    return_top_T: bool = False,
):

    # Get the fused chunksize
    n_leaves = source_term.shape[0]
    chunksize, n_levels_fused = get_fused_chunksize_2D(p, jnp.complex128, n_leaves)
    n_chunks = n_leaves // chunksize
    logging.debug(
        "_fused_local_solve_and_build_2D_ItI: n_leaves = %i, chunksize = %i, n_chunks = %i, n_levels_fused = %i",
        n_leaves,
        chunksize,
        n_chunks,
        n_levels_fused,
    )

    # early exit if n_chunks == 1:
    if n_chunks == 1:
        # Do local solve stage
        R_arr, Y_arr, h_arr, v_arr = _local_solve_stage_2D_ItI(
        D_xx=D_xx,
        D_xy=D_xy,
        D_yy=D_yy,
        D_x=D_x,
        D_y=D_y,
        I_P_0=I_P_0,
        Q_I=Q_I,
        F=F,
        G=G,
        p=p,
        D_xx_coeffs=D_xx_coeffs,
        D_yy_coeffs=D_yy_coeffs,
        D_xy_coeffs=D_xy_coeffs,
        D_x_coeffs=D_x_coeffs,
        D_y_coeffs=D_y_coeffs,
        I_coeffs=I_coeffs,
        source_term=source_term,
        host_device=host_device,
        )
        # Do build stage
        return  _uniform_build_stage_2D_ItI(
        R_maps=R_arr, h_arr=h_arr, l=l, host_device=host_device, return_ItI=return_top_T
        )

    T_arr_lst = []
    h_arr_lst = []
    soln_lst = []
    get_all_operators = bdry_data is not None
    if get_all_operators:
        logging.debug("_fused_local_solve_and_build_2D_ItI: bdry_data shape = %s", bdry_data.shape)
        # Figure out chunksize for bdry_data
        bdry_data_chunksize = bdry_data.shape[0] // n_chunks
        # These are options that will be passed to build stage.
        bool_subtree_recomp = False
        lowlevel_merge_host_device = DEVICE_ARR[0]
    else:
        # These are options that will be passed to build stage.
        bool_subtree_recomp = True
        lowlevel_merge_host_device = DEVICE_ARR[0]

    # Loop over chunks
    for i in range(0, n_chunks):
        chunk_start_idx = i * chunksize
        chunk_end_idx = min((i + 1) * chunksize, n_leaves)
        # logging.debug(
        #     "_fused_local_solve_and_build_2D_ItI: chunk_start_idx = %i", chunk_start_idx
        # )
        # logging.debug(
        #     "_fused_local_solve_and_build_2D_ItI: chunk_end_idx = %i", chunk_end_idx
        # )
        # Split the input data into a chunk
        source_term_chunk = source_term[chunk_start_idx:chunk_end_idx]
        D_xx_coeffs_chunk = (
            D_xx_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xx_coeffs is not None
            else None
        )
        D_xy_coeffs_chunk = (
            D_xy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xy_coeffs is not None
            else None
        )
        D_yy_coeffs_chunk = (
            D_yy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_yy_coeffs is not None
            else None
        )
        D_x_coeffs_chunk = (
            D_x_coeffs[chunk_start_idx:chunk_end_idx]
            if D_x_coeffs is not None
            else None
        )
        D_y_coeffs_chunk = (
            D_y_coeffs[chunk_start_idx:chunk_end_idx]
            if D_y_coeffs is not None
            else None
        )
        I_coeffs_chunk = (
            I_coeffs[chunk_start_idx:chunk_end_idx] if I_coeffs is not None else None
        )
        R_arr_chunk, Y_arr_chunk, h_arr_chunk, v_chunk = _local_solve_stage_2D_ItI(
            D_xx=D_xx,
            D_xy=D_xy,
            D_yy=D_yy,
            D_x=D_x,
            D_y=D_y,
            I_P_0=I_P_0,
            Q_I=Q_I,
            F=F,
            G=G,
            p=p,
            source_term=source_term_chunk,
            D_xx_coeffs=D_xx_coeffs_chunk,
            D_xy_coeffs=D_xy_coeffs_chunk,
            D_yy_coeffs=D_yy_coeffs_chunk,
            D_x_coeffs=D_x_coeffs_chunk,
            D_y_coeffs=D_y_coeffs_chunk,
            I_coeffs=I_coeffs_chunk,
            host_device=device,
            device=device,
        )

        # Merge the chunk as far as we can
        build_stage_out = _uniform_build_stage_2D_ItI(
            R_arr_chunk,
            h_arr_chunk,
            n_levels_fused,
            subtree_recomp=bool_subtree_recomp,
            host_device=lowlevel_merge_host_device,
            device=device,
        )

        # Either compute the solution or append the data
        # to the output lists
        if get_all_operators:
            S_lst, g_tilde_lst = build_stage_out


            bdry_data_start = i * bdry_data_chunksize
            bdry_data_end = (i + 1) * bdry_data_chunksize

            # Do the down pass
            bdry_data_i = bdry_data[bdry_data_start:bdry_data_end]
            bdry_data_i = jax.device_put(bdry_data_i, DEVICE_ARR[0])

            bdry_data_o = _partial_down_pass_ItI(
                bdry_data_i,
                S_lst,
                g_tilde_lst,
                device=DEVICE_ARR[0],
            )
            # compute interior solutions with Y matrices
            bdry_data_o = bdry_data_o.reshape((-1, bdry_data_o.shape[-1]))
            h_soln_chunk = jnp.einsum("ijk,ik->ij", Y_arr_chunk, bdry_data_o)
            soln_chunk = h_soln_chunk + v_chunk  # TODO: Fix this for multi-source code.

            # Append the solution to the output list
            soln_lst.append(jax.device_put(soln_chunk, host_device))

            # Now delete Y and v. They're not needed and garbage collection is not fast enough.
            Y_arr_chunk.delete()
            v_chunk.delete()

        else:
            T_last, h_last = build_stage_out
            Y_arr_chunk.delete()
            v_chunk.delete()

            # Append T and h to their lists
            T_arr_lst.append(T_last)
            h_arr_lst.append(h_last)


    # Return the solution or the rest of the merge information
    if get_all_operators:
        # The solutions were computed in the loop. Just need to concatenate
        # them and return.
        soln = jnp.concatenate(soln_lst, axis=0)
        return soln
    else:
        # In this branch, the boundary data is not specified, so we need
        # to keep merging and return all of the merge information.
        T_arr = jnp.concatenate(T_arr_lst, axis=0)
        # for T in T_arr_lst:
        #     T.delete()

        h_arr = jnp.concatenate(h_arr_lst, axis=0)
        # for h in h_arr_lst:
        #     h.delete()

        logging.debug("_fused_local_solve_and_build_2D_ItI: starting final merge")
        # Final call to build_stage to get top-level information
        return  _uniform_build_stage_2D_ItI(
            T_arr, h_arr, l - n_levels_fused, device=device, host_device=device, return_ItI=return_top_T
        )
        # logging.debug(
        #     "_fused_local_solve_and_build_2D_ItI: returning S_arr_lst with shapes: %s",
        #     [S.shape for S in S_arr_lst],
        # )
        # return S_arr_lst, g_tilde_lst


def _down_pass_from_fused(
    bdry_data: jax.Array,
    S_arr_lst: List[jax.Array],
    v_int_lst: List[jax.Array],
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    P: jax.Array,
    Q_D: jax.Array,
    sidelens: jax.Array,
    l: int,
    p: int,
    source_term: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    device: jax.Device = DEVICE_ARR[0],
) -> jax.Array:
    

    # First do a partial down pass
    bdry_data = _partial_down_pass(
        bdry_data,
        S_arr_lst,
        v_int_lst,
        device=device,
    )

    # Then do the rest of the down pass in the
    # fused solve and build methods
    soln = _fused_local_solve_and_build_2D(
        D_xx=D_xx,
        D_xy=D_xy,
        D_yy=D_yy,
        D_x=D_x,
        D_y=D_y,
        P=P,
        Q_D=Q_D,
        sidelens=sidelens,
        l=l,
        p=p,
        source_term=source_term,
        D_xx_coeffs=D_xx_coeffs,
        D_xy_coeffs=D_xy_coeffs,
        D_yy_coeffs=D_yy_coeffs,
        D_x_coeffs=D_x_coeffs,
        D_y_coeffs=D_y_coeffs,
        I_coeffs=I_coeffs,
        bdry_data=bdry_data,
    )

    return soln


def _partial_down_pass(
    bdry_data: jax.Array,
    S_arr_lst: List[jax.Array],
    v_int_lst: List[jax.Array],
    device: jax.Device = DEVICE_ARR[0],
    host_device: jax.Device = HOST_DEVICE,
) -> jax.Array:
    bdry_data = jax.device_put(bdry_data, device)
    S_arr_lst = [jax.device_put(S, device) for S in S_arr_lst]
    v_int_lst = [jax.device_put(v_int, device) for v_int in v_int_lst]
    n_levels = len(S_arr_lst)

    logging.debug("_partial_down_pass: bdry_data.devices: %s", bdry_data.devices())
    logging.debug(
        "_partial_down_pass: S_arr_lst[0].devices: %s", S_arr_lst[0].devices()
    )
    # Reshape bdry_data to (1, n_bdry) if necessary
    if bdry_data.ndim == 1:
        bdry_data = bdry_data.reshape((1, -1))

    # First do a few levels of the down pass
    for level in range(n_levels - 1, -1, -1):

        S_arr = S_arr_lst[level]
        v_int = v_int_lst[level]

        logging.debug(
            "_partial_down_pass: S_arr %s, v_int: %s, bdry_data: %s",
            S_arr.shape,
            v_int.shape,
            bdry_data.shape,
        )

        # print("_partial_down_pass: S_arr.devices() = ", S_arr.devices())
        # print("_partial_down_pass: v_int.devices() = ", v_int.devices())
        # print("_partial_down_pass: bdry_data.devices() = ", bdry_data.devices())

        bdry_data = vmapped_propogate_down_quad(S_arr, bdry_data, v_int)
        # Reshape from (-1, 4, n_bdry) to (-1, n_bdry)
        n_bdry = bdry_data.shape[-1]
        bdry_data = bdry_data.reshape((-1, n_bdry))

        # Delete arrays we no longer need
        S_arr.delete()
        v_int.delete()

    bdry_data = jax.device_put(bdry_data, host_device)

    return bdry_data


def _down_pass_from_fused_ItI(
    bdry_data: jax.Array,
    S_arr_lst: List[jax.Array],
    f_lst: List[jax.Array],
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    I_P_0: jax.Array,
    Q_I: jax.Array,
    F: jax.Array,
    G: jax.Array,
    l: int,
    p: int,
    source_term: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    device: jax.Device = DEVICE_ARR[0],
) -> None:

    bdry_data = jax.device_put(bdry_data, device)
    # print("_down_pass_from_fused_ItI: bdry_data shape: ", bdry_data.shape)

    # First do a partial down pass
    bdry_data = _partial_down_pass_ItI(
        bdry_data,
        S_arr_lst,
        f_lst,
        device=device,
    )
    # print("_down_pass_from_fused_ItI: bdry_data shape: ", bdry_data.shape)

    # Then do the rest of the down pass in the
    # fused solve and build methods
    soln = _fused_local_solve_and_build_2D_ItI(
        D_xx=D_xx,
        D_xy=D_xy,
        D_yy=D_yy,
        D_x=D_x,
        D_y=D_y,
        I_P_0=I_P_0,
        Q_I=Q_I,
        F=F,
        G=G,
        l=l,
        p=p,
        source_term=source_term,
        D_xx_coeffs=D_xx_coeffs,
        D_xy_coeffs=D_xy_coeffs,
        D_yy_coeffs=D_yy_coeffs,
        D_x_coeffs=D_x_coeffs,
        D_y_coeffs=D_y_coeffs,
        I_coeffs=I_coeffs,
        bdry_data=bdry_data,
        device=device,
        host_device=device,
    )

    return soln


def _partial_down_pass_ItI(
    boundary_imp_data: jax.Array,
    S_maps_lst: List[jax.Array],
    f_lst: List[jax.Array],
    device: jax.Device = DEVICE_ARR[0],
) -> jax.Array:

    n_levels = len(S_maps_lst)
    if boundary_imp_data.ndim == 1:
        bdry_data = boundary_imp_data.reshape((1, -1))
    else:
        bdry_data = boundary_imp_data
    # print("_partial_down_pass_ItI: bdry_data shape: ", bdry_data.shape)

    # logging.debug("_partial_down_pass_ItI: input boundary_imp_data shape: %s", boundary_imp_data.shape)
    # Propogate the Dirichlet data down the tree using the S maps.
    for level in range(n_levels - 1, -1, -1):
        # n_bdry = bdry_data.shape[-1]

        # bdry_data = bdry_data.reshape((-1, n_bdry))

        S_arr = S_maps_lst[level]
        f = f_lst[level]

        # logging.debug("_partial_down_pass_ItI: level=%s, S_arr=%s, f=%s, bdry_data=%s", level, S_arr.shape, f.shape, bdry_data.shape)

        bdry_data = vmapped_propogate_down_quad_ItI(S_arr, bdry_data, f)
        n_bdry = bdry_data.shape[-1]
        bdry_data = bdry_data.reshape((-1, n_bdry))
    # Delete data we no longer need
    for S in S_maps_lst:
        S.delete()
    for f in f_lst:
        f.delete()

    return bdry_data


def _fused_all_single_chunk(
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    P: jax.Array,
    Q_D: jax.Array,
    l: int,
    source_term: jax.Array,
    sidelens: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    bdry_data: jax.Array | None = None,
    host_device: jax.Device = DEVICE_ARR[0],
) -> jax.Array:

    p = (P.shape[0] // 4) + 1

    Y_arr, DtN_arr, v_arr, v_prime_arr = _local_solve_stage_2D(
        D_xx=D_xx,
        D_xy=D_xy,
        D_yy=D_yy,
        D_x=D_x,
        D_y=D_y,
        P=P,
        sidelens=sidelens,
        p=p,
        D_xx_coeffs=D_xx_coeffs,
        D_yy_coeffs=D_yy_coeffs,
        D_xy_coeffs=D_xy_coeffs,
        D_x_coeffs=D_x_coeffs,
        D_y_coeffs=D_y_coeffs,
        I_coeffs=I_coeffs,
        source_term=source_term,
        host_device=host_device,
        Q_D=Q_D,
        uniform_grid=True,
    )
    bdry_data = jax.device_put(bdry_data, DEVICE_ARR[0])

    # Do build stage
    S_arr_lst, v_arr_lst = _uniform_build_stage_2D_DtN(
        DtN_maps=DtN_arr, v_prime_arr=v_prime_arr, l=l, host_device=host_device
    )

    # Do down pass
    bdry_data = _partial_down_pass(
        bdry_data, S_arr_lst, v_arr_lst, host_device=DEVICE_ARR[0]
    )

    # Compute homogeneous solution
    h_soln = jnp.einsum("ijk,ik->ij", Y_arr, bdry_data)
    soln = h_soln + v_arr
    return jax.device_put(soln, host_device)


def _fused_all_single_chunk_ItI(
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    I_P_0: jax.Array,
    Q_I: jax.Array,
    F: jax.Array,
    G: jax.Array,
    l: int,
    p: int,
    source_term: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    bdry_data: jax.Array | None = None,
    host_device: jax.Device = DEVICE_ARR[0],
) -> jax.Array:

    R_arr, Y_arr, h_arr, v_arr = _local_solve_stage_2D_ItI(
        D_xx=D_xx,
        D_xy=D_xy,
        D_yy=D_yy,
        D_x=D_x,
        D_y=D_y,
        I_P_0=I_P_0,
        Q_I=Q_I,
        F=F,
        G=G,
        p=p,
        D_xx_coeffs=D_xx_coeffs,
        D_yy_coeffs=D_yy_coeffs,
        D_xy_coeffs=D_xy_coeffs,
        D_x_coeffs=D_x_coeffs,
        D_y_coeffs=D_y_coeffs,
        I_coeffs=I_coeffs,
        source_term=source_term,
        host_device=host_device,
    )
    bdry_data = jax.device_put(bdry_data, DEVICE_ARR[0])

    # Do build stage
    S_arr_lst,  f_arr_lst = _uniform_build_stage_2D_ItI(
        R_maps=R_arr, h_arr=h_arr, l=l, host_device=host_device
    )

    # Do down pass
    bdry_data = _partial_down_pass_ItI(bdry_data, S_arr_lst, f_arr_lst)
    bdry_data = bdry_data.reshape(-1, bdry_data.shape[-1])

    # Compute homogeneous solution
    logging.debug(
        "_fused_all_single_chunk_ItI: Y_arr shape: %s, and bdry_data shape: %s",
        Y_arr.shape,
        bdry_data.shape,
    )
    h_soln = jnp.einsum("ijk,ik->ij", Y_arr, bdry_data)
    soln = h_soln + v_arr  # TODO: Fix this for multi-source code.
    return jax.device_put(soln, host_device)


def _baseline_recomputation_upward_pass(
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    P: jax.Array,
    Q_D: jax.Array,
    sidelens: jax.Array,
    l: int,
    p: int,
    source_term: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    host_device: jax.Device = HOST_DEVICE,
) -> Tuple[List[jax.Array], List[jax.Array]]:

    # Get the fused chunksize
    n_leaves = source_term.shape[0]
    chunksize, _ = get_fused_chunksize_2D(p, jnp.float64, n_leaves)
    n_chunks = n_leaves // chunksize
    logging.debug(
        "_baseline_recomputation_upward_pass: n_leaves = %i, chunksize = %i, n_chunks = %i",
        n_leaves,
        chunksize,
        n_chunks,
    )
    DtN_arr_lst = []
    v_prime_arr_lst = []
    # Loop over chunks
    for i in range(0, n_chunks):
        chunk_start_idx = i * chunksize
        chunk_end_idx = min((i + 1) * chunksize, n_leaves)
        # Split the input data into a chunk
        source_term_chunk = source_term[chunk_start_idx:chunk_end_idx]
        sidelens_chunk = sidelens[chunk_start_idx:chunk_end_idx]
        D_xx_coeffs_chunk = (
            D_xx_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xx_coeffs is not None
            else None
        )
        D_xy_coeffs_chunk = (
            D_xy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xy_coeffs is not None
            else None
        )
        D_yy_coeffs_chunk = (
            D_yy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_yy_coeffs is not None
            else None
        )
        D_x_coeffs_chunk = (
            D_x_coeffs[chunk_start_idx:chunk_end_idx]
            if D_x_coeffs is not None
            else None
        )
        D_y_coeffs_chunk = (
            D_y_coeffs[chunk_start_idx:chunk_end_idx]
            if D_y_coeffs is not None
            else None
        )
        I_coeffs_chunk = (
            I_coeffs[chunk_start_idx:chunk_end_idx] if I_coeffs is not None else None
        )
        Y_arr_chunk, DtN_arr_chunk, v_chunk, v_prime_chunk = _local_solve_stage_2D(
            D_xx=D_xx,
            D_xy=D_xy,
            D_yy=D_yy,
            D_x=D_x,
            D_y=D_y,
            P=P,
            Q_D=Q_D,
            sidelens=sidelens_chunk,
            p=p,
            source_term=source_term_chunk,
            D_xx_coeffs=D_xx_coeffs_chunk,
            D_xy_coeffs=D_xy_coeffs_chunk,
            D_yy_coeffs=D_yy_coeffs_chunk,
            D_x_coeffs=D_x_coeffs_chunk,
            D_y_coeffs=D_y_coeffs_chunk,
            I_coeffs=I_coeffs_chunk,
            host_device=DEVICE_ARR[0],
            uniform_grid=True,
        )

        v_chunk.delete()
        Y_arr_chunk.delete()

        # Move the DtN_arr_chunk and v_prime_chunk to the host memory
        DtN_arr_lst.append(jax.device_put(DtN_arr_chunk, HOST_DEVICE))
        v_prime_arr_lst.append(jax.device_put(v_prime_chunk, HOST_DEVICE))
        DtN_arr_chunk.delete()
        v_prime_chunk.delete()
    # Compute the rest of the merge stages
    DtN_arr = jnp.concatenate(DtN_arr_lst, axis=0)
    for DtN in DtN_arr_lst:
        DtN.delete()
    v_prime_arr = jnp.concatenate(v_prime_arr_lst, axis=0)
    for v_prime in v_prime_arr_lst:
        v_prime.delete()
    logging.debug("_baseline_recomputation_upward_pass: starting final merges")
    # Final call to build_stage to get top-level information
    S_arr_lst,  v_arr_lst = _uniform_build_stage_2D_DtN(
        DtN_arr, v_prime_arr, l, host_device=HOST_DEVICE
    )

    # Return S_arr_lst and v_arr_lst to host memory
    # S_arr_lst_out = [jax.device_put(S, HOST_DEVICE) for S in S_arr_lst]
    # v_int_out = [jax.device_put(v, HOST_DEVICE) for v in v_arr_lst]
    # return S_arr_lst_out, v_int_out

    return S_arr_lst, v_arr_lst


def _baseline_recomputation_downward_pass(
    D_xx: jax.Array,
    D_xy: jax.Array,
    D_yy: jax.Array,
    D_x: jax.Array,
    D_y: jax.Array,
    P: jax.Array,
    Q_D: jax.Array,
    sidelens: jax.Array,
    l: int,
    p: int,
    source_term: jax.Array,
    S_arr_lst: List[jax.Array],
    v_int_lst: List[jax.Array],
    bdry_data: jax.Array,
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
    host_device: jax.Device = HOST_DEVICE,
) -> Tuple[List[jax.Array], List[jax.Array]]:

    bdry_data = jax.device_put(bdry_data, DEVICE_ARR[0])
    # Do a partial down pass
    leaf_bdry_data = _partial_down_pass(
        bdry_data=bdry_data,
        S_arr_lst=S_arr_lst,
        v_int_lst=v_int_lst,
    )

    # Get the fused chunksize
    n_leaves = source_term.shape[0]
    chunksize, n_levels_fused = get_fused_chunksize_2D(p, jnp.float64, n_leaves)
    n_chunks = n_leaves // chunksize
    logging.debug(
        "_fused_local_solve_and_build_2D: n_leaves = %i, chunksize = %i, n_chunks = %i, n_levels_fused = %i",
        n_leaves,
        chunksize,
        n_chunks,
        n_levels_fused,
    )
    DtN_arr_lst = []
    v_prime_arr_lst = []
    soln_lst = []
    # Loop over chunks
    for i in range(0, n_chunks):
        chunk_start_idx = i * chunksize
        chunk_end_idx = min((i + 1) * chunksize, n_leaves)
        # Split the input data into a chunk
        source_term_chunk = source_term[chunk_start_idx:chunk_end_idx]
        # sidelens_chunk = sidelens[chunk_start_idx:chunk_end_idx]

        leaf_bdry_data_chunk = leaf_bdry_data[chunk_start_idx:chunk_end_idx]
        leaf_bdry_data_chunk = jax.device_put(leaf_bdry_data_chunk, DEVICE_ARR[0])
        D_xx_coeffs_chunk = (
            D_xx_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xx_coeffs is not None
            else None
        )
        D_xy_coeffs_chunk = (
            D_xy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_xy_coeffs is not None
            else None
        )
        D_yy_coeffs_chunk = (
            D_yy_coeffs[chunk_start_idx:chunk_end_idx]
            if D_yy_coeffs is not None
            else None
        )
        D_x_coeffs_chunk = (
            D_x_coeffs[chunk_start_idx:chunk_end_idx]
            if D_x_coeffs is not None
            else None
        )
        D_y_coeffs_chunk = (
            D_y_coeffs[chunk_start_idx:chunk_end_idx]
            if D_y_coeffs is not None
            else None
        )
        I_coeffs_chunk = (
            I_coeffs[chunk_start_idx:chunk_end_idx] if I_coeffs is not None else None
        )

        # this does a linear system solve rather than recomputing the entire T and Y matrices.
        soln_chunk = _local_solutions_2D_DtN_uniform(
            D_xx=D_xx,
            D_xy=D_xy,
            D_yy=D_yy,
            D_x=D_x,
            D_y=D_y,
            P=P,
            Q_D=Q_D,
            p=p,
            bdry_data=leaf_bdry_data_chunk,
            source_term=source_term_chunk,
            D_xx_coeffs=D_xx_coeffs_chunk,
            D_xy_coeffs=D_xy_coeffs_chunk,
            D_yy_coeffs=D_yy_coeffs_chunk,
            D_x_coeffs=D_x_coeffs_chunk,
            D_y_coeffs=D_y_coeffs_chunk,
            I_coeffs=I_coeffs_chunk,
            host_device=DEVICE_ARR[0],
        )

        soln_lst.append(jax.device_put(soln_chunk, host_device))
    soln = jnp.concatenate(soln_lst, axis=0)
    return soln
