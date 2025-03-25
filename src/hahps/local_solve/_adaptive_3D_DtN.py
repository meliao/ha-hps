import jax.numpy as jnp
import jax

from .._pdeproblem import PDEProblem
from .._domain import get_all_leaves

from .._device_config import DEVICE_ARR, HOST_DEVICE
from typing import Tuple
from .._precompute_operators_3D import precompute_Q_3D_DtN
from ._adaptive_2D_DtN import vmapped_get_DtN_adaptive, assemble_diff_operator
from ._uniform_3D_DtN import _gather_coeffs_3D
from functools import partial


def local_solve_stage_adaptive_3D_DtN(
    pde_problem: PDEProblem,
    host_device: jax.Device = HOST_DEVICE,
    device: jax.Device = DEVICE_ARR[0],
) -> Tuple[jax.Array, jax.Array, jax.Array, jax.Array]:
    coeffs_gathered, which_coeffs = _gather_coeffs_3D(
        D_xx_coeffs=pde_problem.D_xx_coefficients,
        D_xy_coeffs=pde_problem.D_xy_coefficients,
        D_yy_coeffs=pde_problem.D_yy_coefficients,
        D_xz_coeffs=pde_problem.D_xz_coefficients,
        D_yz_coeffs=pde_problem.D_yz_coefficients,
        D_zz_coeffs=pde_problem.D_zz_coefficients,
        D_x_coeffs=pde_problem.D_x_coefficients,
        D_y_coeffs=pde_problem.D_y_coefficients,
        D_z_coeffs=pde_problem.D_z_coefficients,
        I_coeffs=pde_problem.I_coefficients,
    )
    source_term = pde_problem.source
    source_term = jax.device_put(source_term, device)

    # stack the precomputed differential operators into a single array
    diff_ops = jnp.stack(
        [
            pde_problem.D_xx,
            pde_problem.D_xy,
            pde_problem.D_yy,
            pde_problem.D_xz,
            pde_problem.D_yz,
            pde_problem.D_zz,
            pde_problem.D_x,
            pde_problem.D_y,
            pde_problem.D_z,
            jnp.eye(pde_problem.domain.p**3),
        ]
    )
    # Now that arrays are in contiguous blocks of memory, we can move them to the GPU.
    diff_ops = jax.device_put(diff_ops, device)
    coeffs_gathered = jax.device_put(coeffs_gathered, device)
    # Have to generate Q matrices for each leaf by re-scaling the differential
    # operators
    sidelens = jnp.array(
        [l.xmax - l.xmin for l in get_all_leaves(pde_problem.domain.root)]
    )
    sidelens = jax.device_put(sidelens, device)

    all_diff_operators, Q_Ds = (
        vmapped_prep_nonuniform_refinement_diff_operators_3D(
            sidelens,
            coeffs_gathered,
            which_coeffs,
            diff_ops,
            pde_problem.domain.p,
            pde_problem.domain.q,
        )
    )
    Y_arr, T_arr, v, h = vmapped_get_DtN_adaptive(
        source_term, all_diff_operators, Q_Ds, pde_problem.P
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


@partial(jax.jit, static_argnums=(4, 5))
def _prep_nonuniform_refinement_diff_operators_3D(
    sidelen: jnp.array,
    coeffs_arr: jnp.array,
    which_coeffs: jnp.array,
    diff_ops_3D: jnp.array,
    p: int,
    q: int,
) -> Tuple[jnp.array, jnp.array]:
    """
    Prepares the differential operators for nonuniform refinement.

    The differential operators are, in order:

    D_xx, D_xy, D_yy, D_xz, D_yz, D_zz, D_x, D_y, D_z, I

    Args:
        sidelen (jnp.array): Array of shape (n_leaves,) containing the sidelengths of each leaf.
        coeffs_arr (jnp.array): Array of shape (?, n_leaves, p**3) containing the PDE coefficients.
        which_coeffs (jnp.array): Array of shape (10,) containing boolean values specifying which coefficients are not None.
        diff_ops_3D (jnp.array): Array of shape (10, p**3, p**3) containing the precomputed differential operators.
        p (int): The number of Chebyshev nodes in each dimension.
        q (int): The number of Gauss nodes in each dimension.

    Returns:
        Tuple[jnp.array, jnp.array]: The precomputed differential operators for nonuniform refinement.
    """
    half_side_len = sidelen / 2
    # Second-order differential operators
    diff_ops_3D = diff_ops_3D.at[:6].set(diff_ops_3D[:6] / (half_side_len**2))
    # First-order differential operators
    diff_ops_3D = diff_ops_3D.at[6:9].set(diff_ops_3D[6:9] / half_side_len)
    diff_operator = assemble_diff_operator(
        coeffs_arr, which_coeffs, diff_ops_3D
    )
    Q_D = precompute_Q_3D_DtN(
        p, q, diff_ops_3D[6], diff_ops_3D[7], diff_ops_3D[8]
    )
    return diff_operator, Q_D


vmapped_prep_nonuniform_refinement_diff_operators_3D = jax.vmap(
    _prep_nonuniform_refinement_diff_operators_3D,
    in_axes=(0, 1, None, None, None, None),
    out_axes=(0, 0),
)
