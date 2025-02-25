""" OpenMDAO class definition for IndepVarComp"""

import collections
from six import string_types, iteritems

from openmdao.core.component import Component

class IndepVarComp(Component):
    """A Component that provides an output to connect to a parameter.

    Options
    -------
    fd_options['force_fd'] :  bool(False)
        Set to True to finite difference this system.
    fd_options['form'] :  str('forward')
        Finite difference mode. (forward, backward, central) You can also set to 'complex_step' to peform the complex step method if your components support it.
    fd_options['step_size'] :  float(1e-06)
        Default finite difference stepsize
    fd_options['step_type'] :  str('absolute')
        Set to absolute, relative
    """

    def __init__(self, name, val=None, **kwargs):
        super(IndepVarComp, self).__init__()

        if isinstance(name, string_types):
            if val is None:
                raise ValueError('IndepVarComp init: a value must be provided as the second arg.')
            self.add_output(name, val, **kwargs)

        elif isinstance(name, collections.Iterable):
            for tup in name:
                badtup = None
                if isinstance(tup, tuple):
                    if len(tup) == 3:
                        n, v, kw = tup
                    elif len(tup) == 2:
                        n, v = tup
                        kw = {}
                    else:
                        badtup = tup
                else:
                    badtup = tup
                if badtup:
                    if isinstance(badtup, string_types):
                        badtup = name
                    raise ValueError("IndepVarComp init: arg %s must be a tuple of the form "
                                     "(name, value) or (name, value, keyword_dict)." %
                                     str(badtup))
                self.add_output(n, v, **kw)
        else:
            raise ValueError("first argument to IndepVarComp init must be either of type "
                             "`str` or an iterable of tuples of the form (name, value) or "
                             "(name, value, keyword_dict).")

    def _sys_apply_linear(self, mode, ls_inputs=None, vois=(None, ), gs_outputs=None):
        """For `IndepVarComp`, just pass on the incoming values.

        Args
        ----
        mode : string
            Derivative mode, can be 'fwd' or 'rev'.

        ls_inputs : dict
            We can only solve derivatives for the inputs the instigating
            system has access to. (Not used here.)

        vois: list of strings
            List of all quantities of interest to key into the mats.

        gs_outputs : dict, optional
            Linear Gauss-Siedel can limit the outputs when calling apply.
        """
        if mode == 'fwd':
            sol_vec, rhs_vec = self.dumat, self.drmat
            for voi in vois:
                rhs_vec[voi].vec[:] = 0.0
        else:
            sol_vec, rhs_vec = self.drmat, self.dumat

        for voi in vois:
            if gs_outputs is None:
                rhs_vec[voi].vec[:] += sol_vec[voi].vec
            else:
                for var, meta in iteritems(self.dumat[voi]):
                    if var in gs_outputs[voi]:
                        rhs_vec[voi][var] += sol_vec[voi][var]

    def _sys_linearize(self, params, unknowns, resids, force_fd=False): 
        """ No linearization needed for this one""" 
        # added to avoid the small overhead from calling the parent implementation 
        # because this class has nothing to do 
        pass

    def solve_nonlinear(self, params, unknowns, resids):
        """ Performs no operation. """
        pass
