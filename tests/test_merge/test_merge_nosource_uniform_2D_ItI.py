import numpy as np

from hahps.merge._nosource_uniform_2D_ItI import (
    nosource_merge_stage_uniform_2D_ItI,
    _nosource_uniform_quad_merge_ItI,
)

from hahps.local_solve._nosource_uniform_2D_ItI import (
    nosource_local_solve_stage_uniform_2D_ItI,
)
from hahps._discretization_tree import DiscretizationNode2D
from hahps._domain import Domain
from hahps._pdeproblem import PDEProblem


class Test_nosource_merge_stage_uniform_2D_ItI:
    def test_0(self) -> None:
        """Tests the function returns without error."""
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

        Y_arr, T_arr = nosource_local_solve_stage_uniform_2D_ItI(pde_problem=t)

        assert Y_arr.shape == (n_leaves, p**2, 4 * q)
        # n_leaves, n_bdry, _ = DtN_arr.shape
        # DtN_arr = DtN_arr.reshape((int(n_leaves / 2), 2, n_bdry, n_bdry))
        # v_prime_arr = v_prime_arr.reshape((int(n_leaves / 2), 2, 4 * t.q))

        S_arr_lst, D_inv_lst, BD_inv_lst = nosource_merge_stage_uniform_2D_ItI(
            T_arr=T_arr, l=l
        )
        print(
            "test_0: S_arr_lst shapes = ", [S_arr.shape for S_arr in S_arr_lst]
        )

        assert len(S_arr_lst) == l
        assert len(D_inv_lst) == l
        assert len(BD_inv_lst) == l

        for i in range(l):
            print("test_0: i=", i)
            print("test_0: S_arr_lst[i].shape = ", S_arr_lst[i].shape)
            # print("test_0: f_arr_lst[i].shape = ", f_arr_lst[i].shape)
            assert S_arr_lst[i].shape[-2] == D_inv_lst[i].shape[-2]

        # Check the shapes of the bottom-level output arrays
        n_quads = (n_leaves // 4) // 4
        assert S_arr_lst[0].shape == (4 * n_quads, 8 * q, 8 * q)
        # assert f_arr_lst[0].shape == (4 * n_quads, 8 * q)

        # Check the shapes of the middle-level output arrays.
        n_bdry = 16 * q
        n_interface = 16 * q
        assert S_arr_lst[1].shape == (4, n_interface, n_bdry)
        # assert f_arr_lst[1].shape == (4, n_interface)

        # Check the shapes of the top-level output arrays.
        n_root_bdry = t.domain.boundary_points.shape[0]
        n_root_interface = n_root_bdry
        assert S_arr_lst[2].shape == (1, n_root_interface, n_root_bdry)
        # assert f_arr_lst[2].shape == (1, n_root_interface)


class Test__uniform_quad_merge_ItI:
    def test_0(self) -> None:
        n_bdry = 28
        n_bdry_int = n_bdry // 4
        n_bdry_ext = 2 * (n_bdry // 4)
        T_a = np.random.normal(size=(n_bdry, n_bdry))
        T_b = np.random.normal(size=(n_bdry, n_bdry))
        T_c = np.random.normal(size=(n_bdry, n_bdry))
        T_d = np.random.normal(size=(n_bdry, n_bdry))

        print("test_0: T_a shape: ", T_a.shape)
        S, R, D_inv, BD_inv = _nosource_uniform_quad_merge_ItI(
            T_a, T_b, T_c, T_d
        )

        assert S.shape == (8 * n_bdry_int, 4 * n_bdry_ext)
        assert R.shape == (4 * n_bdry_ext, 4 * n_bdry_ext)
        assert D_inv.shape == (8 * n_bdry_int, 8 * n_bdry_int)
        assert BD_inv.shape == (4 * n_bdry_ext, 8 * n_bdry_int)
