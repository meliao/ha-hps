import jax.numpy as jnp
import jax

from .._pdeproblem import PDEProblem
from .._device_config import DEVICE_ARR, HOST_DEVICE
from typing import Tuple
import logging


def local_solve_stage_uniform_2D_DtN(
    pde_problem: PDEProblem,
    host_device: jax.Device = HOST_DEVICE,
    device: jax.Device = DEVICE_ARR[0],
) -> Tuple[jax.Array, jax.Array, jax.Array, jax.Array]:
    """This function performs the local solve stage of the HPS algorithm.

    Args:
        D_xx (jax.Array): Precomputed differential operator with shape (p**2, p**2).
        D_xy (jax.Array): Precomputed differential operator with shape (p**2, p**2).
        D_yy (jax.Array): Precomputed differential operator with shape (p**2, p**2).
        D_x (jax.Array): Precomputed differential operator with shape (p**2, p**2).
        D_y (jax.Array): Precomputed differential operator with shape (p**2, p**2).
        P (jax.Array): Precomputed interpolation operator with shape (4(p-1), 4q).
            Maps data on the boundary Gauss nodes to data on the boundary Chebyshev nodes.
            Used when computing DtN maps.
        p (int): Shape parameter. Number of Chebyshev nodes along one dimension in a leaf.
        source_term (jax.Array): Has shape (n_leaves, p**2). The right-hand side of the PDE.
        sidelens (jax.Array): Has shape (n_leaves,). Gives the side length of each leaf. Used for scaling differential operators.
        D_xx_coeffs (jax.Array | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_xy_coeffs (jax.Array | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_yy_coeffs (jax.Array | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_x_coeffs (jax.Array | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        D_y_coeffs (jax.Array | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        I_coeffs (jax.Array | None, optional): Has shape (n_leaves, p**2). Defaults to None, which means zero coeffs.
        device (jax.Device, optional): Device where computation should be executed.
        host_device (jax.Device, optional): Device where results should be returned.
        uniform_grid (bool, optional): If True, uses an optimized version of the local solve stage which assumes all of the
            leaves have the same size. If False (default), does a bit of extra computation which depends on sidelens.
        Q_D (jax.Array): Precomputed interpolation + differentiation operator with shape (4q, p**2).
            Maps the solution on the Chebyshev nodes to the normal derivatives on the boundary Gauss nodes.
            Used when computing DtN maps. Only used if uniform_grid == True.
    Returns:
         Tuple[jax.Array, jax.Array, jax.Array, jax.Array]:
            Y_arr, T_arr, v, h.
            Y_arr is an array of shape (n_leaves, p**2, 4q) containing the Dirichlet-to-Soln maps for each leaf.
            T_arr is an array of shape (n_quad_merges, 4, 4q, 4q) containing the DtN maps for each leaf.
            v is an array of shape (n_leaves, p**2) containing the particular solutions for each leaf.
            h is an array of shape (n_quad_merges, 4, 4q) containing the boundary fluxes for each leaf
    """
    logging.debug("local_solve_stage_uniform_2D_DtN: started")

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
    logging.debug(
        "local_solve_stage_uniform_2D_DtN: input source_term devices = %s",
        source_term.devices(),
    )
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

    # Put the input data on the device
    coeffs_gathered = jax.device_put(
        coeffs_gathered,
        device,
    )
    diff_operators = vmapped_assemble_diff_operator(
        coeffs_gathered, which_coeffs, diff_ops
    )
    Y_arr, T_arr, v, h = vmapped_get_DtN_uniform(
        source_term, diff_operators, pde_problem.Q, pde_problem.P
    )

    # Return data to the requested device
    T_arr_host = jax.device_put(T_arr, host_device)
    del T_arr
    v_host = jax.device_put(v, host_device)
    del v
    h_host = jax.device_put(h, host_device)
    del h
    Y_arr_host = jax.device_put(Y_arr, host_device)
    del Y_arr

    # Return the DtN arrays, particular solutions, particular
    # solution fluxes, and the solution operators. The solution
    # operators are not moved to the host.
    return Y_arr_host, T_arr_host, v_host, h_host


@jax.jit
def _gather_coeffs_2D(
    D_xx_coeffs: jax.Array | None = None,
    D_xy_coeffs: jax.Array | None = None,
    D_yy_coeffs: jax.Array | None = None,
    D_x_coeffs: jax.Array | None = None,
    D_y_coeffs: jax.Array | None = None,
    I_coeffs: jax.Array | None = None,
) -> Tuple[jax.Array, jax.Array]:
    """If not None, expects each input to have shape (n_leaf_nodes, p**2).

    Returns:
        Tuple[jax.Array, jax.Array]: coeffs_gathered and which_coeffs
            coeffs_gathered is an array of shape (?, n_leaf_nodes, p**2) containing the non-None coefficients.
            which_coeffs is an array of shape (6) containing boolean values specifying which coefficients were not None.
    """
    coeffs_lst = [
        D_xx_coeffs,
        D_xy_coeffs,
        D_yy_coeffs,
        D_x_coeffs,
        D_y_coeffs,
        I_coeffs,
    ]
    which_coeffs = jnp.array([coeff is not None for coeff in coeffs_lst])
    coeffs_gathered = jnp.array(
        [coeff for coeff in coeffs_lst if coeff is not None]
    )
    return coeffs_gathered, which_coeffs


@jax.jit
def _add(
    out: jax.Array,
    coeff: jax.Array,
    diff_op: jax.Array,
) -> jax.Array:
    """One branch of add_or_not. Expects out to have shape (p**2, p**2), coeff has shape (p**2), diff_op has shape (p**2, p**2)."""
    # res = out + jnp.diag(coeff) @ diff_op
    res = out + jnp.einsum("ab,a->ab", diff_op, coeff)
    return res


@jax.jit
def _not(out: jax.Array, coeff: jax.Array, diff_op: jax.Array) -> jax.Array:
    return out


@jax.jit
def add_or_not(
    i: int,
    carry_tuple: Tuple[jax.Array, jax.Array, jax.Array, jax.Array, int],
) -> Tuple[jax.Array, jax.Array, jax.Array, jax.Array]:
    """body function for loop in assemble_diff_operator."""
    out = carry_tuple[0]
    coeffs_arr = carry_tuple[1]
    diff_ops = carry_tuple[2]
    which_coeffs = carry_tuple[3]
    counter = carry_tuple[4]

    out = jax.lax.cond(
        which_coeffs[i],
        _add,
        _not,
        out,
        coeffs_arr[counter],
        diff_ops[i],
    )
    counter = jax.lax.cond(
        which_coeffs[i],
        lambda x: x + 1,
        lambda x: x,
        counter,
    )
    return (out, coeffs_arr, diff_ops, which_coeffs, counter)


@jax.jit
def assemble_diff_operator(
    coeffs_arr: jax.Array,
    which_coeffs: jax.Array,
    diff_ops: jax.Array,
) -> jax.Array:
    """Given an array of coefficients, this function assembles the differential operator.

    Args:
        coeffs_arr (jax.Array): Has shape (?, p**2).
        which_coeffs (jax.Array): Has shape (5,) or (9,) and specifies which coefficients are not None.
        diff_ops (jax.Array): Has shape (6, p**2, p**2). Contains the precomputed differential operators.

    Returns:
        jax.Array: Has shape (p**2, p**2).
    """

    n_loops = which_coeffs.shape[0]

    out = jnp.zeros_like(diff_ops[0])

    # Commenting this out because it is very memory intensive
    counter = 0
    init_val = (out, coeffs_arr, diff_ops, which_coeffs, counter)
    out, _, _, _, _ = jax.lax.fori_loop(0, n_loops, add_or_not, init_val)

    # Semantically the same as this:
    # counter = 0
    # for i in range(n_loops):
    #     if which_coeffs[i]:
    #         # out += jnp.diag(coeffs_arr[counter]) @ diff_ops[i]
    #         out += jnp.einsum("ab,a->ab", diff_ops[i], coeffs_arr[counter])
    #         counter += 1

    return out


vmapped_assemble_diff_operator = jax.vmap(
    assemble_diff_operator,
    in_axes=(1, None, None),
    out_axes=0,
)


@jax.jit
def get_DtN(
    source_term: jax.Array,
    diff_operator: jax.Array,
    Q: jax.Array,
    P: jax.Array,
) -> Tuple[jax.Array]:
    """
    Args:
        source_term (jax.Array): Array of size (p**2,) containing the source term.
        diff_operator (jax.Array): Array of size (p**2, p**2) containing the local differential operator defined on the
                    Cheby grid.
        Q (jax.Array): Array of size (4q, p**2) containing the matrix interpolating from a soln on the interior
                    to that soln's boundary fluxes on the Gauss boundary.
        P (jax.Array): Array of size (4(p-1), 4q) containing the matrix interpolating from the Gauss to the Cheby boundary.

    Returns:
        Tuple[jax.Array, jax.Array]:
            Y (jax.Array): Matrix of size (p**2, 4q). This is the "DtSoln" map,
                which maps incoming Dirichlet data on the boundary Gauss nodes to the solution on the Chebyshev nodes.
            T (jax.Array): Matrix of size (4q, 4q). This is the "DtN" map, which maps incoming Dirichlet
                data on the boundary Gauss nodes to the normal derivatives on the boundary Gauss nodes.
            v (jax.Array): Array of size (p**2,) containing the particular solution.
            h (jax.Array): Array of size (4q,) containing the outgoing boundary normal derivatives of the particular solution.
    """
    n_cheby_bdry = P.shape[0]

    A_ii = diff_operator[n_cheby_bdry:, n_cheby_bdry:]
    A_ii_inv = jnp.linalg.inv(A_ii)
    # A_ie shape (n_cheby_int, n_cheby_bdry)
    A_ie = diff_operator[n_cheby_bdry:, :n_cheby_bdry]
    L_2 = jnp.zeros((diff_operator.shape[0], n_cheby_bdry), dtype=jnp.float64)
    L_2 = L_2.at[:n_cheby_bdry].set(jnp.eye(n_cheby_bdry))
    soln_operator = -1 * A_ii_inv @ A_ie
    L_2 = L_2.at[n_cheby_bdry:].set(soln_operator)
    Y = L_2 @ P
    T = Q @ Y

    v = jnp.zeros((diff_operator.shape[0],), dtype=jnp.float64)
    v = v.at[n_cheby_bdry:].set(A_ii_inv @ source_term[n_cheby_bdry:])
    h = Q @ v

    return Y, T, v, h


vmapped_get_DtN_uniform = jax.vmap(
    get_DtN,
    in_axes=(0, 0, None, None),
    out_axes=(0, 0, 0, 0),
)
