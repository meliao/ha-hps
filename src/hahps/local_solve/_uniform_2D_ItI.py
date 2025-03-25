import jax.numpy as jnp
import jax

from .._pdeproblem import PDEProblem
from .._device_config import DEVICE_ARR, HOST_DEVICE
from ._uniform_2D_DtN import _gather_coeffs_2D, vmapped_assemble_diff_operator
from typing import Tuple
import logging


def local_solve_stage_uniform_2D_ItI(
    pde_problem: PDEProblem,
    host_device: jax.Device = HOST_DEVICE,
    device: jax.Device = DEVICE_ARR[0],
) -> Tuple[jax.Array, jax.Array, jax.Array, jax.Array]:
    """
    Performs the local solve stage for all of the leaves in the 2D domain.

    Args:
        D_xx (jnp.ndarray): Precomputed differential operator with shape (p**2, p**2).
        D_xy (jnp.ndarray): Precomputed differential operator with shape (p**2, p**2).
        D_yy (jnp.ndarray): Precomputed differential operator with shape (p**2, p**2).
        D_x (jnp.ndarray): Precomputed differential operator with shape (p**2, p**2).
        D_y (jnp.ndarray): Precomputed differential operator with shape (p**2, p**2).
        I_P_0 (jnp.ndarray): Precomputed interpolation operator with shape (4(p-1), 4q).
            Maps data on the Gauss boundary nodes to data on the Cheby boundary nodes.
            Is formed by taking the kronecker product of I and P_0, which is the standard
            Gauss -> Cheby 1D interp matrix missing the last row.
        Q_D (jnp.ndarray): Precomputed interpolation operator with shape (4q, p**2).
            Maps functions on the boundary Cheby nodes (counting corners twice) to functions on the
            boundary Gauss nodes.
        G (jnp.ndarray): Precomputed differentiation operator with shape (4q, p**2).
            Maps a function on the Chebyshev nodes to the function's outgoing impedance
            on the boundary Cheby nodes, counting corners twice.
        F (jnp.ndarray): Precomputed differentiation operator with shape (4(p-1), p**2).
            Maps a function on the Chebyshev nodes to the function's incoming impedance
            on the boundary Cheby nodes, counting corners once.
        p (int): Shape parameter. Number of Chebyshev nodes along one dimension in a leaf.
        source_term (jnp.ndarray): Has shape (n_leaves, p**2, n_src). The right-hand side of the PDE.
        D_xx_coeffs (jnp.ndarray | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_xy_coeffs (jnp.ndarray | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_yy_coeffs (jnp.ndarray | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_x_coeffs (jnp.ndarray | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_y_coeffs (jnp.ndarray | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        I_coeffs (jnp.ndarray | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.

    Returns:
        Tuple[jnp.ndarray, jnp.ndarray]: _description_
    """
    logging.debug("_local_solve_stage_2D_ItI: started")

    # Gather the coefficients into a single array.
    coeffs_gathered, which_coeffs = _gather_coeffs_2D(
        D_xx_coeffs=pde_problem.D_xx_coefficients,
        D_xy_coeffs=pde_problem.D_xy_coefficients,
        D_yy_coeffs=pde_problem.D_yy_coefficients,
        D_x_coeffs=pde_problem.D_x_coefficients,
        D_y_coeffs=pde_problem.D_y_coefficients,
        I_coeffs=pde_problem.I_coefficients,
    )
    source_term = pde_problem.source
    source_term = jax.device_put(
        source_term,
        device,
    )
    # stack the precomputed differential operators into a single array
    diff_ops = jnp.stack(
        [
            pde_problem.D_xx,
            pde_problem.D_xy,
            pde_problem.D_yy,
            pde_problem.D_x,
            pde_problem.D_y,
            jnp.eye(pde_problem.D_xx.shape[0], dtype=jnp.float64),
        ]
    )

    coeffs_gathered = jax.device_put(
        coeffs_gathered,
        device,
    )

    all_diff_operators = vmapped_assemble_diff_operator(
        coeffs_gathered, which_coeffs, diff_ops
    )

    # TODO: Expand the code to work with multiple sources.
    # Make sure source term has shape (n_leaves, p**2, n_src)
    if len(source_term.shape) == 2:
        source_term = jnp.expand_dims(source_term, axis=-1)

    # R_arr, Y_arr, outgoing_part_impedance_arr, part_soln_arr = (
    #     vmapped_get_ItI_then_rearrange(
    #         diff_operator=all_diff_operators,
    #         source_term=source_term,
    #         I_P_0=I_P_0,
    #         Q_I=Q_I,
    #         F=F,
    #         G=G,
    #     )
    # )
    R_arr, Y_arr, h, v = vmapped_get_ItI(
        all_diff_operators,
        source_term,
        pde_problem.P,
        pde_problem.QH,
        pde_problem.G,
    )

    R_arr_host = jax.device_put(R_arr, host_device)
    Y_arr_host = jax.device_put(Y_arr, host_device)

    # The indexing on the last axis is to take away the multi-source dimension
    # TODO: Expand the code to work with multiple sources.
    h_arr_host = jax.device_put(h, host_device)[..., 0]
    v_arr_host = jax.device_put(v, host_device)[..., 0]

    return (
        Y_arr_host,
        R_arr_host,
        v_arr_host,
        h_arr_host,
    )


@jax.jit
def get_ItI(
    diff_operator: jax.Array,
    source_term: jax.Array,
    P: jax.Array,
    QH: jax.Array,
    G: jax.Array,
) -> Tuple[jax.Array, jax.Array, jax.Array, jax.Array]:
    """Given the coefficients specifying a partial differential operator on a leaf, this function
    computes the particular solution, particular solution boundary fluxes, the
    impedance to impedance map, and the impedance to solution map.

    Args:
        coeffs_arr (jax.Array): Has shape (?, p**2). Specifies the PDE coefficients.
        source_term (jax.Array): Has shape (p**2, n_sources). Specifies the RHS of the PDE.
        diff_ops (jax.Array): Has shape (5, p**2, p**2). Contains the precomputed differential operators. In 3D,
                                this has shape (9, p**3, p**3).
        which_coeffs (jax.Array): Has shape (5,) and specifies which coefficients are not None.
        P (jax.Array): Has shape (4(p-1), 4q). Maps data on the Gauss boundary nodes to data on the Cheby boundary nodes.
                            Is formed by taking the kronecker product of I and P_0, which is the standard
                            Gauss -> Cheby 1D interp matrix missing the last row.
        QH (jax.Array): Has shape (4q, p**2). Maps a function on the Chebyshev nodes to the function's outgoing impedance
                    on the boundary Gauss nodes.
        G (jax.Array): Has shape (4(p-1), p**2). Maps a function on the Chebyshev nodes to the function's incoming
                    impedance on the boundary Cheby nodes, counting corners once.

    Returns:
        Tuple[jax.Array]:
            T (jax.Array): Has shape (4q, 4q). This is the "ItI" operator, which maps incoming impedance data on the
                boundary Gauss nodes to the outgoing impedance data on the boundary Gauss nodes.
            Y (jax.Array): Has shape (p**2, 4q). This is the interior solution operator, which maps from incoming impedance
                data on the boundary Gauss nodes to the resulting homogeneous solution on the Chebyshev nodes.
            h (jax.Array): Has shape (4q, n_sources). This is the outgoing impedance data on the
                boundary Gauss nodes, due to the particular solution(s).
            v (jax.Array): Has shape (p**2, n_sources). This is the particular solution(s) on the Chebyshev nodes.
    """
    # print("get_ItI: I_P_0 shape: ", I_P_0.shape)
    # print("get_ItI: Q_I shape: ", Q_I.shape)
    n_cheby_pts = diff_operator.shape[-1]
    n_cheby_bdry_pts = P.shape[0]
    A = diff_operator

    # B has shape (n_cheby_pts, n_cheby_pts). Its top rows are F and its bottom rows are the
    # bottom rows of A.
    B = jnp.zeros((n_cheby_pts, n_cheby_pts), dtype=jnp.complex128)
    B = B.at[:n_cheby_bdry_pts].set(G)
    B = B.at[n_cheby_bdry_pts:].set(A[n_cheby_bdry_pts:])
    B_inv = jnp.linalg.inv(B)

    # Phi has shape (n_cheby_pts, n_cheby_interior_pts). It maps from the source
    # term evaluated on the interior Cheby nodes to the particular soln on all of
    # the Cheby nodes.
    Phi = B_inv[:, n_cheby_bdry_pts:]

    # Y has shape (n_cheby_pts, n_cheby_bdry_pts). It maps from
    # incoming impedance data on the boundary G-L nodes to the
    # homogeneous solution on all of the Cheby nodes.
    Y = B_inv[:, :n_cheby_bdry_pts] @ P

    source_int = source_term[n_cheby_bdry_pts:]
    v = Phi @ source_int
    # part_soln = part_soln.at[:n_cheby_bdry_pts].set(0.0)
    h = QH @ v

    # Interpolate to Gauss nodes
    T = QH @ Y
    return (T, Y, h, v)


vmapped_get_ItI = jax.vmap(
    get_ItI,
    in_axes=(0, 0, None, None, None),
    out_axes=(0, 0, 0, 0),
)
