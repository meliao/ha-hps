from hahps._pdeproblem import PDEProblem
import jax.numpy as jnp

from hahps._discretization_tree import (
    DiscretizationNode2D,
    DiscretizationNode3D,
)
from hahps._domain import Domain


class Test_PDEProblem_init:
    def test_0(self) -> None:
        """2D DtN initialization."""

        # Create a 2D uniform domain
        xmin = 0.0
        xmax = 1.0
        ymin = 0.0
        ymax = 1.0
        p = 6
        q = 4
        L = 2
        root = DiscretizationNode2D(
            xmin=xmin,
            xmax=xmax,
            ymin=ymin,
            ymax=ymax,
        )
        domain = Domain(p=p, q=q, root=root, L=L)

        source = jnp.zeros_like(domain.interior_points[..., 0])
        D_xx_coefficients = jnp.zeros_like(domain.interior_points[..., 0])

        pde_problem = PDEProblem(
            domain=domain, source=source, D_xx_coefficients=D_xx_coefficients
        )

        # Check the shape of the precomputed operators.
        assert pde_problem.D_x.shape == (p**2, p**2)
        assert pde_problem.P.shape == (4 * (p - 1), 4 * q)
        assert pde_problem.Q.shape == (4 * q, p**2)

    def test_1(self) -> None:
        """2D ItI Initialization"""

        # Create a 2D uniform domain
        xmin = 0.0
        xmax = 1.0
        ymin = 0.0
        ymax = 1.0
        p = 6
        q = 4
        L = 2
        root = DiscretizationNode2D(
            xmin=xmin,
            xmax=xmax,
            ymin=ymin,
            ymax=ymax,
        )
        domain = Domain(p=p, q=q, root=root, L=L)

        source = jnp.zeros_like(domain.interior_points[..., 0])
        D_xx_coefficients = jnp.zeros_like(domain.interior_points[..., 0])

        pde_problem = PDEProblem(
            domain=domain,
            source=source,
            D_xx_coefficients=D_xx_coefficients,
            use_ItI=True,
            eta=1.0,
        )

        # Check the shape of the precomputed operators.
        assert pde_problem.D_x.shape == (p**2, p**2)
        assert pde_problem.P.shape == (4 * (p - 1), 4 * q)
        assert pde_problem.G.shape == (4 * (p - 1), p**2)
        assert pde_problem.QH.shape == (4 * q, p**2)

    def test_2(self) -> None:
        """3D DtN initialization."""

        # Create a 2D uniform domain
        xmin = 0.0
        xmax = 1.0
        ymin = 0.0
        ymax = 1.0
        zmin = 0.0
        zmax = 1.0
        p = 6
        q = 4
        L = 2
        root = DiscretizationNode3D(
            xmin=xmin,
            xmax=xmax,
            ymin=ymin,
            ymax=ymax,
            zmin=zmin,
            zmax=zmax,
        )
        domain = Domain(p=p, q=q, root=root, L=L)

        source = jnp.zeros_like(domain.interior_points[..., 0])
        D_xx_coefficients = jnp.zeros_like(domain.interior_points[..., 0])

        pde_problem = PDEProblem(
            domain=domain, source=source, D_xx_coefficients=D_xx_coefficients
        )

        # Check the shape of the precomputed operators.
        assert pde_problem.D_x.shape == (p**3, p**3)
        assert pde_problem.P.shape == (p**3 - (p - 2) ** 3, 6 * q**2)
        assert pde_problem.Q.shape == (6 * q**2, p**3)
