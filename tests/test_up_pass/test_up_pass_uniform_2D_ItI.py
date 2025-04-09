import jax.numpy as jnp

import numpy as np
from hahps.up_pass._uniform_2D_ItI import (
    up_pass_uniform_2D_ItI,
    assemble_boundary_data,
)
from hahps._discretization_tree import DiscretizationNode2D
from hahps._domain import Domain
from hahps._pdeproblem import PDEProblem
from hahps.merge._nosource_uniform_2D_ItI import (
    nosource_merge_stage_uniform_2D_ItI,
)

from hahps.local_solve._nosource_uniform_2D_ItI import (
    nosource_local_solve_stage_uniform_2D_ItI,
)
import logging


class Test_assemble_boundary_data:
    def test_0(self, caplog) -> None:
        """Tests return shapes are correct."""

        nside = 3
        h_in = jnp.zeros((4, 4 * nside))
        D_inv = jnp.zeros((8 * nside, 8 * nside))
        BD_inv = jnp.zeros((8 * nside, 8 * nside))

        h, g_tilde = assemble_boundary_data(h_in, D_inv, BD_inv)
        assert h.shape == (8 * nside,)
        assert g_tilde.shape == (8 * nside,)


class Test_up_pass_uniform_2D_ItI:
    def test_0(self, caplog) -> None:
        """Tests to make sure things run without error."""
        caplog.set_level(logging.DEBUG)
        p = 6
        q = 4
        l = 3
        eta = 4.0

        root = DiscretizationNode2D(
            xmin=0.0,
            xmax=1.0,
            ymin=0.0,
            ymax=1.0,
        )
        domain = Domain(p=p, q=q, root=root, L=l)
        n_leaves = 4**l

        d_xx_coeffs = np.random.normal(size=(n_leaves, p**2))
        print("test_0: d_xx_coeffs = ", d_xx_coeffs.shape)

        t = PDEProblem(
            domain=domain,
            D_xx_coefficients=d_xx_coeffs,
            use_ItI=True,
            eta=eta,
        )

        T_arr, Y_arr = nosource_local_solve_stage_uniform_2D_ItI(pde_problem=t)

        assert Y_arr.shape == (n_leaves, p**2, 4 * q)
        # n_leaves, n_bdry, _ = DtN_arr.shape
        # DtN_arr = DtN_arr.reshape((int(n_leaves / 2), 2, n_bdry, n_bdry))
        # v_prime_arr = v_prime_arr.reshape((int(n_leaves / 2), 2, 4 * t.q))

        S_arr_lst, D_inv_lst, BD_inv_lst = nosource_merge_stage_uniform_2D_ItI(
            T_arr=T_arr, l=l
        )

        t.D_inv_lst = D_inv_lst
        t.BD_inv_lst = BD_inv_lst

        logging.debug(
            "test_0: D_inv_lst shapes = %s", [s.shape for s in D_inv_lst]
        )
        logging.debug(
            "test_0: BD_inv_lst shapes = %s", [s.shape for s in BD_inv_lst]
        )

        source = jnp.ones_like(domain.interior_points[..., 0])

        # Do the upward pass
        v, g_tilde_lst = up_pass_uniform_2D_ItI(
            source=source,
            pde_problem=t,
        )

        assert v.shape == (n_leaves, p**2)
        assert len(g_tilde_lst) == l
