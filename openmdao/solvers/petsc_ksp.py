""" OpenMDAO LinearSolver that uses PetSC KSP to solve for a system's
derivatives. This solver can be used under MPI."""

from __future__ import print_function
from six import iteritems

import os

# TODO: Do we have to make this solver with a factory?
import petsc4py
from petsc4py import PETSc
import numpy as np

from openmdao.solvers.solver_base import LinearSolver

trace = os.environ.get("OPENMDAO_TRACE")
if trace:  # pragma: no cover
    from openmdao.core.mpi_wrap import debug


def _get_petsc_vec_array_new(vec):
    """ helper function to handle a petsc backwards incompatibility between 3.6
    and older versions."""

    return vec.getArray(readonly=True)


def _get_petsc_vec_array_old(vec):
    """ helper function to handle a petsc backwards incompatibility between 3.6
    and older versions."""

    return vec.getArray()

try:
    petsc_version = petsc4py.__version__
except AttributeError:  # hack to fix doc-tests
    petsc_version = "3.5"

if int((petsc_version).split('.')[1]) >= 6:
    _get_petsc_vec_array = _get_petsc_vec_array_new
else:
    _get_petsc_vec_array = _get_petsc_vec_array_old


# This class object is given to KSP as a callback object for printing the residual.
class Monitor(object):
    """ Prints output from PETSc's KSP solvers """

    def __init__(self, ksp):
        """ Stores pointer to the ksp solver """
        self._ksp = ksp
        self._norm0 = 1.0

    def __call__(self, ksp, counter, norm):
        """ Store norm if first iteration, and print norm """
        if counter == 0 and norm != 0.0:
            self._norm0 = norm

        ksp = self._ksp
        ksp.iter_count += 1

        if ksp.options['iprint'] > 0:
            ksp.print_norm('KSP', ksp.system.pathname, ksp.iter_count, norm,
                           self._norm0, indent=1, solver='LN')


class PetscKSP(LinearSolver):
    """ OpenMDAO LinearSolver that uses PetSC KSP to solve for a system's
    derivatives. This solver can be used under MPI.

    Options
    -------
    options['atol'] :  float(1e-12)
        Absolute convergence tolerance.
    options['iprint'] :  int(0)
        Set to 0 to disable printing, set to 1 to print the residual to stdout each iteration, set to 2 to print subiteration residuals as well.
    options['maxiter'] :  int(100)
        Maximum number of iterations.
    options['mode'] :  str('auto')
        Derivative calculation mode, set to 'fwd' for forward mode, 'rev' for reverse mode, or 'auto' to let OpenMDAO determine the best mode.
    options['rtol'] :  float(1e-12)
        Relative convergence tolerance.

    """

    def __init__(self):
        super(PetscKSP, self).__init__()

        opt = self.options
        opt.add_option('atol', 1e-12,
                       desc='Absolute convergence tolerance.')
        opt.add_option('rtol', 1e-12,
                       desc='Relative convergence tolerance.')
        opt.add_option('maxiter', 100,
                       desc='Maximum number of iterations.')
        opt.add_option('mode', 'auto', values=['fwd', 'rev', 'auto'],
                       desc="Derivative calculation mode, set to 'fwd' for " +
                       "forward mode, 'rev' for reverse mode, or 'auto' to " +
                       "let OpenMDAO determine the best mode.")

        # These are defined whenever we call solve to provide info we need in
        # the callback.
        self.system = None
        self.voi = None
        self.mode = None

        self.ksp = None

    def setup(self, system):
        """ Setup petsc problem just once."""

        lsize = np.sum(system._local_unknown_sizes[None][system.comm.rank, :])
        size = np.sum(system._local_unknown_sizes[None])
        jac_mat = PETSc.Mat().createPython([(lsize, size), (lsize, size)],
                                           comm=system.comm)
        jac_mat.setPythonContext(self)
        jac_mat.setUp()

        if trace:  # pragma: no cover
            debug("creating KSP object for system",system.pathname)
        self.ksp = PETSc.KSP().create(comm=system.comm)
        self.ksp.setOperators(jac_mat)
        self.ksp.setType('fgmres')
        self.ksp.setGMRESRestart(1000)
        self.ksp.setPCSide(PETSc.PC.Side.RIGHT)
        self.ksp.setMonitor(Monitor(self))

        if trace:  # pragma: no cover
            debug("ksp.getPC()")
            debug("rhs_buf, sol_buf size: %d" % lsize)
        pc_mat = self.ksp.getPC()
        pc_mat.setType('python')
        pc_mat.setPythonContext(self)
        if trace:  # pragma: no cover
            debug("ksp setup done")

        self.rhs_buf = np.zeros((lsize, ))
        self.sol_buf = np.zeros((lsize, ))

    def solve(self, rhs_mat, system, mode):
        """ Solves the linear system for the problem in self.system. The
        full solution vector is returned.

        Args
        ----
        rhs_mat : dict of ndarray
            Dictionary containing one ndarry per top level quantity of
            interest. Each array contains the right-hand side for the linear
            solve.

        system : `System`
            Parent `System` object.

        mode : string
            Derivative mode, can be 'fwd' or 'rev'.

        Returns
        -------
        dict of ndarray : Solution vectors
        """
        options = self.options
        self.mode = mode

        self.ksp.setTolerances(max_it=options['maxiter'],
                               atol=options['atol'],
                               rtol=options['rtol'])

        unknowns_mat = {}
        for voi, rhs in iteritems(rhs_mat):

            sol_vec = np.zeros(rhs.shape)
            # Set these in the system
            if trace:  # pragma: no cover
                debug("creating sol_buf petsc vec for voi", voi)
            self.sol_buf_petsc = PETSc.Vec().createWithArray(sol_vec,
                                                             comm=system.comm)
            if trace:  # pragma: no cover
                debug("creating rhs_buf petsc vec for voi", voi)
            self.rhs_buf_petsc = PETSc.Vec().createWithArray(rhs,
                                                             comm=system.comm)

            # Petsc can only handle one right-hand-side at a time for now
            self.voi = voi
            self.system = system
            self.iter_count = 0
            self.ksp.solve(self.rhs_buf_petsc, self.sol_buf_petsc)
            self.system = None

            if self.options['iprint'] > 0:
                if self.iter_count == self.options['maxiter']:
                    msg = 'FAILED to converge after hitting max iterations'
                else:
                    msg = 'Converged'
                    self.print_norm('KSP', system.pathname, self.iter_count,
                                    0, 0, msg=msg, solver='LN')

            unknowns_mat[voi] = sol_vec

            #print system.name, 'Linear solution vec', d_unknowns

        self.system = None
        return unknowns_mat

    def mult(self, mat, arg, result):
        """ KSP Callback: applies Jacobian matrix. Mode is determined by the
        system.

        Args
        ----
        arg : PetSC Vector
            Incoming vector

        result : PetSC Vector
            Empty array into which we place the matrix-vector product.
        """

        system = self.system
        mode = self.mode

        voi = self.voi
        if mode == 'fwd':
            sol_vec, rhs_vec = system.dumat[voi], system.drmat[voi]
        else:
            sol_vec, rhs_vec = system.drmat[voi], system.dumat[voi]

        # Set incoming vector
        # sol_vec.vec[:] = arg.array
        sol_vec.vec[:] = _get_petsc_vec_array(arg)

        # Start with a clean slate
        rhs_vec.vec[:] = 0.0
        system.clear_dparams()

        system._sys_apply_linear(mode, ls_inputs=self.system._ls_inputs, vois=(voi,))

        result.array[:] = rhs_vec.vec

        # print("arg", arg.array)
        # print("result", result.array)

    def apply(self, mat, sol_vec, rhs_vec):
        """ Applies preconditioner

        Args
        ----
        sol_vec : PetSC Vector
            Incoming vector

        rhs_vec : PetSC Vector
            Empty vector into which we return the preconditioned sol_vec
        """

        # TODO - Preconditioning is not supported yet, so mimic an Identity
        # matrix.
        # if int((petsc4py.__version__).split('.')[1]) >= 6:
        #     vec = sol_vec.getArray(readonly=True)
        # else:
        #     vec = sol_vec.getArray()

        rhs_vec.array[:] = _get_petsc_vec_array(sol_vec)
