===================
Solution methods
===================

The package offers a few different ways to interact with the HPS algorithms, and exposes routines for performing different stages of the HPS method for different discretization types. 



Building the Fast Direct Solver
==================================

In many use cases, one wants to construct a direct solver and then apply it to many different boundary conditions sequentially. The pair of functions :func:`hahps.build_solver` and :func:`hahps.solve` are designed to facilitate this.

.. note::
   :func:`hahps.build_solver` by default moves data from the GPU to the CPU while building the solution operator. This can significantly slow down the time required to build the solver. If only one solution is required, the subtree recomputation methods may be preferrable.


.. autofunction:: hahps.build_solver

.. autofunction:: hahps.solve


Subtree-Recomputation Solution Methods
========================================

To take full advantage of hardware acceleration, we designed subtree-recomputation methods. These methods are useful when you want to solve the PDE for a single right-hand-side very quickly. If you want to impose a Dirichlet or Robin boundary condition and know the boundary data at runtime, you can use :func:`hahps.solve_subtree`. 
Otherwise, you can use the pair of functions :func:`hahps.upward_pass_subtree` and :func:`hahps.downward_pass_subtree`. The upward pass returns the domain's Poincare--Steklov operator, which can be used, for example, to define a boundary integral equation specifying boundary data. The downward pass uses the partially-saved solution operators and performs recomputation where necessary.

.. autofunction:: hahps.solve_subtree

.. autofunction:: hahps.upward_pass_subtree

.. autofunction:: hahps.downward_pass_subtree



Individual HPS Algorithm Stages
================================

Local Solve Stage
-----------------

.. automodule:: hahps.local_solve
   :members:

Merge Stage
------------

.. automodule:: hahps.merge
   :members:



Downward Pass
----------------


.. automodule:: hahps.down_pass
   :members:
