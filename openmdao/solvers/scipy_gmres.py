""" OpenMDAO LinearSolver that uses Scipy's GMRES to solve for derivatives."""

from __future__ import print_function

from six import iteritems

import numpy as np
from scipy.sparse.linalg import gmres, LinearOperator

from openmdao.solvers.solver_base import LinearSolver


class ScipyGMRES(LinearSolver):
    """ Scipy's GMRES Solver. This is a serial solver, so
    it should never be used in an MPI setting.

    Options
    -------
    options['atol'] :  float(1e-12)
        Absolute convergence tolerance.
    options['iprint'] :  int(0)
        Set to 0 to disable printing, set to 1 to print the residual to stdout each iteration, set to 2 to print subiteration residuals as well.
    options['maxiter'] :  int(1000)
        Maximum number of iterations.
    options['mode'] :  str('auto')
        Derivative calculation mode, set to 'fwd' for forward mode, 'rev' for reverse mode, or 'auto' to let OpenMDAO determine the best mode.
    options['precondition'] :  bool(False)
        Set to True to turn on preconditioning.

    """

    def __init__(self):
        super(ScipyGMRES, self).__init__()

        opt = self.options
        opt.add_option('atol', 1e-12,
                       desc='Absolute convergence tolerance.')
        opt.add_option('maxiter', 1000,
                       desc='Maximum number of iterations.')
        opt.add_option('mode', 'auto', values=['fwd', 'rev', 'auto'],
                       desc="Derivative calculation mode, set to 'fwd' for " +
                       "forward mode, 'rev' for reverse mode, or 'auto' to " +
                       "let OpenMDAO determine the best mode.")
        opt.add_option('precondition', False,
                       desc='Set to True to turn on preconditioning.')

        # These are defined whenever we call solve to provide info we need in
        # the callback.
        self.system = None
        self.voi = None
        self.mode = None
        self._norm0 = 0.0

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

        unknowns_mat = {}
        for voi, rhs in iteritems(rhs_mat):

            # Scipy can only handle one right-hand-side at a time.
            self.voi = voi

            n_edge = len(rhs)
            A = LinearOperator((n_edge, n_edge),
                               matvec=self.mult,
                               dtype=float)

            # Support a preconditioner
            if self.options['precondition'] == True:
                M = LinearOperator((n_edge, n_edge),
                                   matvec=self.precon,
                                   dtype=float)
            else:
                M = None

            # Call GMRES to solve the linear system
            self.system = system
            self.iter_count = 0
            d_unknowns, info = gmres(A, rhs, M=M,
                                     tol=options['atol'],
                                     maxiter=options['maxiter'],
                                     callback=self.monitor)
            self.system = None

            if info > 0:
                msg = "Solve in '{}': gmres failed to converge " \
                                      "after {} iterations"
                print(msg.format(system.name, options['maxiter']))
                #logger.error(msg, system.name, info)
                msg = 'FAILED to converge after max iterations'
            elif info < 0:
                msg = "ERROR in solve in '{}': gmres failed"
                print(msg.format(system.name))
                #logger.error(msg, system.name)
                msg = 'ERROR returned from GMRES'
            else:
                msg = 'Converged'

            if self.options['iprint'] > 0:
                self.print_norm('GMRES', system.pathname, self.iter_count,
                                0, 0, msg=msg, solver='LN')

            unknowns_mat[voi] = d_unknowns

            #print(system.name, 'Linear solution vec', d_unknowns)


        return unknowns_mat

    def mult(self, arg):
        """ GMRES Callback: applies Jacobian matrix. Mode is determined by the
        system.

        Args
        ----
        arg : ndarray
            Incoming vector

        Returns
        -------
        ndarray : Matrix vector product of arg with jacobian
        """

        system = self.system
        mode = self.mode

        voi = self.voi
        if mode == 'fwd':
            sol_vec, rhs_vec = system.dumat[voi], system.drmat[voi]
        else:
            sol_vec, rhs_vec = system.drmat[voi], system.dumat[voi]

        # Set incoming vector
        sol_vec.vec[:] = arg

        # Start with a clean slate
        rhs_vec.vec[:] = 0.0
        system.clear_dparams()

        system._sys_apply_linear(mode, ls_inputs=self.system._ls_inputs, vois=(voi,))

        #print("arg", arg)
        #print("result", rhs_vec.vec)
        return rhs_vec.vec

    def precon(self, arg):
        """ GMRES Callback: applies a preconditioner by calling
        solve_nonlinear on this system's children.

        Args
        ----
        arg : ndarray
            Incoming vector

        Returns
        -------
        ndarray : Preconditioned vector
        """

        system = self.system
        mode = self.mode

        voi = self.voi
        if mode == 'fwd':
            sol_vec, rhs_vec = system.dumat[voi], system.drmat[voi]
        else:
            sol_vec, rhs_vec = system.drmat[voi], system.dumat[voi]

        # Set incoming vector
        rhs_vec.vec[:] = arg[:]

        # Start with a clean slate
        system.clear_dparams()

        dumat = {}
        dumat[voi] = system.dumat[voi]
        drmat = {}
        drmat[voi] = system.drmat[voi]

        system.solve_linear(dumat, drmat, (voi, ), mode=mode, precon=True)

        #print("arg", arg)
        #print("preconditioned arg", precon_rhs)
        return sol_vec.vec

    def monitor(self, res):
        """ GMRES Callback: Prints the current residual norm.

        Args
        ----
        res : ndarray
            Current residual.
        """

        if self.options['iprint'] > 0:
            f_norm = np.linalg.norm(res)
            if self.iter_count == 0:
                if f_norm != 0.0:
                    self._norm0 = f_norm
                else:
                    self._norm0 = 1.0
            self.print_norm('GMRES', self.system.pathname, self.iter_count,
                            f_norm, self._norm0, indent=1, solver='LN')
            self.iter_count += 1
