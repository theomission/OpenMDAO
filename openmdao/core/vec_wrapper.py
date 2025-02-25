""" Class definition for VecWrapper"""

import sys
import numpy
from numpy.linalg import norm
from six import iteritems, itervalues, iterkeys
from six.moves import cStringIO

from collections import OrderedDict, namedtuple
from openmdao.util.type_util import is_differentiable
from openmdao.util.string_util import get_common_ancestor

Accessor = namedtuple('Accessor', ['get', 'set', 'flat', 'meta'])

class _ByObjWrapper(object):
    """
    We have to wrap byobj values in these in order to have param vec entries
    that are shared between parents and children all share the same object
    reference, so that when the internal val attribute is changed, all
    `VecWrapper`s that contain a reference to the wrapper will see the updated
    value.
    """
    def __init__(self, val):
        self.val = val

    def __repr__(self):
        return repr(self.val)


class VecWrapper(object):
    """
    A dict-like container of a collection of variables.

    Args
    ----
    pathname : str, optional
        the pathname of the containing `System`

    comm : an MPI communicator (real or fake)
        a communicator that can be used for distributed operations
        when running under MPI.  If not running under MPI, it is
        ignored

    Attributes
    ----------
    idx_arr_type : dtype, optional
        A dtype indicating how index arrays are to be represented.
        The value 'i' indicates an numpy integer array, other
        implementations, e.g., petsc, will define this differently.
    """

    idx_arr_type = 'i'

    def __init__(self, sysdata, comm=None):
        self.comm = comm
        self.vec = None
        self._vardict = OrderedDict()
        self._slices = {}
        self.flat = None

        # Automatic unit conversion in target vectors
        self.deriv_units = False

        self._sysdata = sysdata

    def _flat(self, name):
        """
        Return a flat version of the named variable, including any necessary conversions.
        """
        acc = self._access[name]
        return acc.flat(acc.meta)

    def metadata(self, name):
        """
        Returns the metadata for the named variable.

        Args
        ----
        name : str
            Name of variable to get the metadata for.

        Returns
        -------
        dict
            The metadata dict for the named variable.

        Raises
        -------
        KeyError
            If the named variable is not in this vector.
        """
        try:
            return self._vardict[name]
        except KeyError as error:
            msg = "Variable '{name}' does not exist".format(name=name)
            raise KeyError(msg)

    def _setup_prom_map(self):
        """
        Sets up the internal dict that maps absolute name to promoted name.
        """
        to_prom_name = self._sysdata._to_prom_name
        to_top = self._sysdata._to_top_prom_name

        for prom_name, meta in iteritems(self):
            to_prom_name[meta['pathname']] = prom_name
            to_top[prom_name] = meta['top_promoted_name']

    def __getitem__(self, name):
        """
        Retrieve unflattened value of named var.

        Args
        ----
        name : str
            Name of variable to get the value for.

        Returns
        -------
            The unflattened value of the named variable.
        """
        acc = self._access[name]
        return acc.get(acc.meta)

    def __setitem__(self, name, value):
        """
        Set the value of the named variable.

        Args
        ----
        name : str
            Name of variable to get the value for.

        value :
            The unflattened value of the named variable.
        """
        acc = self._access[name]
        acc.set(acc.meta, value)

    def __len__(self):
        """
        Returns
        -------
            The number of keys (variables) in this vector.
        """
        return len(self._vardict)

    def __contains__(self, key):
        """
        Returns
        -------
            A boolean indicating if the given key (variable name) is in this vector.
        """

        return key in self._vardict

    def __iter__(self):
        """
        Returns
        -------
            A dictionary iterator over the items in _vardict.
        """
        return self._vardict.__iter__()

    def keys(self):
        """
        Returns
        -------
        list or KeyView (python 3)
            the keys (variable names) in this vector.
        """
        return self._vardict.keys()

    def iterkeys(self):
        """
        Returns
        -------
        iter of str
            the keys (variable names) in this vector.
        """
        return iterkeys(self._vardict)

    def items(self):
        """
        Returns
        -------
        list of (str, dict)
            List of tuples containing the name and metadata dict for each
            variable.
        """
        return self._vardict.items()

    def iteritems(self):
        """
        Returns
        -------
        iterator
            Iterator returning the name and metadata dict for each variable.
        """
        return iteritems(self._vardict)

    def values(self):
        """
        Returns
        -------
        list of dict
            List containing metadata dict for each variable.
        """
        return self._vardict.values()

    def itervalues(self):
        """
        Returns
        -------
        iter of dict
            Iterator yielding metadata dict for each variable.
        """
        return self._vardict.values()

    def _get_local_idxs(self, name, idx_dict, get_slice=False):
        """
        Returns all of the indices for the named variable in this vector.

        Args
        ----
        name : str
            Name of variable to get the indices for.

        get_slice : bool, optional
            If True, return the idxs as a slice object, if possible.

        Returns
        -------
        size
            The size of the named variable.

        ndarray
            Index array containing all local indices for the named variable.
        """
        try:
            start, end = self._slices[name]
        except KeyError:
            # this happens if 'name' doesn't exist in this process
            return self.make_idx_array(0, 0)

        if name in idx_dict:
            #TODO: possible slice conversion
            idxs = self.to_idx_array(idx_dict[name]) + start
            if idxs.size > (end-start) or max(idxs) >= end:
                raise RuntimeError("Indices of interest specified for '%s'"
                                   "are too large" % name)
            return idxs
        else:
            if get_slice:
                return slice(start, end)
            return self.make_idx_array(start, end)

    def norm(self):
        """
        Calculates the norm of this vector.

        Returns
        -------
        float
            Norm of our internal vector.
        """
        return norm(self.vec)

    def get_view(self, system, comm, varmap):
        """
        Return a new `VecWrapper` that is a view into this one.

        Args
        ----
        system : `System`
            System for which the view is being created.

        comm : an MPI communicator (real or fake)
            A communicator that is used in the creation of the view.

        varmap : dict
            Mapping of variable names in the old `VecWrapper` to the names
            they will have in the new `VecWrapper`.

        Returns
        -------
        `VecWrapper`
            A new `VecWrapper` that is a view into this one.
        """
        view = self.__class__(system._sysdata, comm)
        view_size = 0

        vardict = self._vardict
        start = -1

        # varmap is ordered, in the same order as vardict
        for name, pname in iteritems(varmap):
            if name in vardict:
                meta = vardict[name]
                view._vardict[pname] = meta
                if not meta.get('pass_by_obj') and not meta.get('remote'):
                    pstart, pend = self._slices[name]
                    if start == -1:
                        start = pstart
                        end = pend
                    else:
                        assert pstart == end, \
                               "%s not contiguous in block containing %s" % \
                               (name, varmap.keys())
                    end = pend
                    view._slices[pname] = (view_size, view_size + meta['size'])
                    view_size += meta['size']

        if start == -1: # no items found
            view.vec = self.vec[0:0]
        else:
            view.vec = self.vec[start:end]

        view._setup_prom_map()
        view.setup_flat()
        view._setup_access_functs()

        return view

    def make_idx_array(self, start, end):
        """
        Return an index vector of the right int type for
        the current implementation.

        Args
        ----
        start : int
            The starting index.

        end : int
            The ending index.

        Returns
        -------
        ndarray of idx_arr_type
            index array containing all indices from start up to but
            not including end
        """
        return numpy.arange(start, end, dtype=self.idx_arr_type)

    def to_idx_array(self, indices):
        """
        Given some iterator of indices, return an index array of the
        right int type for the current implementation.

        Args
        ----
        indices : iterator of ints
            An iterator of indices.

        Returns
        -------
        ndarray of idx_arr_type
            Index array containing all of the given indices.

        """
        return numpy.array(indices, dtype=self.idx_arr_type)

    def merge_idxs(self, idxs):
        """
        Return source and target index arrays, built up from
        smaller index arrays.

        Args
        ----
        idxs : array
            Indices.

        Returns
        -------
        ndarray of idx_arr_type
            Index array containing all of the merged indices.

        """
        if len(idxs) == 0:
            return self.make_idx_array(0, 0)

        return numpy.concatenate(idxs)

    def get_promoted_varname(self, abs_name):
        """
        Returns the relative pathname for the given absolute variable
        pathname.

        Args
        ----
        abs_name : str
            Absolute pathname of a variable.

        Returns
        -------
        rel_name : str
            Relative name mapped to the given absolute pathname.
        """
        try:
            return self._sysdata._to_prom_name[abs_name]
        except KeyError:
            raise KeyError("Relative name not found for variable '%s'" % abs_name)

    def get_states(self):
        """
        Returns
        -------
        list
            A list of names of state variables.
        """
        return [n for n, meta in iteritems(self._vardict) if meta.get('state')]

    def _get_vecvars(self):
        """
        Returns
        -------
            A list of names of variables found in our 'vec' array. This includes
            params that are not 'owned' and remote vars, which have size 0 array values.
        """
        return ((n, meta) for n, meta in iteritems(self._vardict)
                            if not meta.get('pass_by_obj'))

    def setup_flat(self):
        """
        Provides a quick way to iterate over vector subviews.

        Returns
        -------
        A list of (name, array) for each local vector variable.
        """
        if self.flat is None:
            self.flat = OrderedDict([(n,m['val']) for n,m in self._get_vecvars()])
        return self.flat

    def get_byobjs(self):
        """
        Returns
        -------
        list
            A list of names of variables that are passed by object rather than
            through scattering entries from one array to another.
        """
        return [(n, meta) for n, meta in iteritems(self._vardict)
                   if meta.get('pass_by_obj')]

    def _scoped_abs_name(self, name):
        """
        Args
        ----
        name : str
            The absolute pathname of a variable.

        Returns
        -------
        str
            The given name as seen from the 'scope' of the `System` that
            contains this `VecWrapper`.
        """
        if self._sysdata.pathname:
            start = len(self._sysdata.pathname)+1
        else:
            start = 0
        return name[start:]

    def dump(self, out_stream=sys.stdout):  # pragma: no cover
        """
        Args
        ----
        out_stream : file_like
            Where to send human readable output. Default is sys.stdout. Set to
            None to return a str.
        """

        if out_stream is None:
            out_stream = cStringIO()
            return_str = True
        else:
            return_str = False

        lens = [len(n) for n in self.keys()]
        nwid = max(lens) if lens else 10
        vlens = [len(repr(self[v])) for v in self.keys()]
        vwid = max(vlens) if vlens else 1
        if len(self.flat) != len(self): # we have some pass by obj
            defwid = 8
        else:
            defwid = 1
        slens = [len('[{0[0]}:{0[1]}]'.format(self._slices[v])) for v in self.keys()
                       if v in self._slices]+[defwid]
        swid = max(slens)

        for v, meta in iteritems(self):
            if meta.get('pass_by_obj') or meta.get('remote'):
                continue
            if v in self._slices:
                uslice = '[{0[0]}:{0[1]}]'.format(self._slices[v])
            else:
                uslice = ''
            template = "{0:<{nwid}} {1:<{swid}} {2:>{vwid}}\n"
            out_stream.write(template.format(v,
                                             uslice,
                                             repr(self[v]),
                                             nwid=nwid,
                                             swid=swid,
                                             vwid=vwid))

        for v, meta in iteritems(self):
            if meta.get('pass_by_obj') and not meta.get('remote'):
                template = "{0:<{nwid}} {1:<{swid}} {2}\n"
                out_stream.write(template.format(v, '(by obj)',
                                                 repr(self[v]),
                                                 nwid=nwid,
                                                 swid=swid))
        if return_str:
            return out_stream.getvalue()

    def _setup_get_funct(self, name):
        """
        Returns a tuple of efficient closures (nonflat and flat) to access
        the named value.
        """

        meta = self._vardict[name]
        val = meta['val']
        flatfunc = None

        if meta.get('remote'):
            return _remote_access_error, _remote_access_error

        if meta.get('pass_by_obj'):
            return _get_pbo, flatfunc

        shape = meta['shape']
        scale, offset = meta.get('unit_conv', (None, None))
        if self.deriv_units:
            offset = 0.0
        is_scalar = shape == 1
        if is_scalar:
            shapes_same = True
        else:
            shapes_same = shape == val.shape

        # No unit conversion.
        # dparams vector does no unit conversion.
        if scale is None or self.deriv_units is True:
            flatfunc = _get_arr
            if is_scalar:
                func = _get_scalar
            elif shapes_same:
                func = flatfunc
            else:
                func = _get_arr_diff_shape

        # We have a unit conversion
        else:
            flatfunc = _get_arr_units
            if is_scalar:
                func = _get_scalar_units
            elif shapes_same:
                func = flatfunc
            else:
                func = _get_arr_units_diff_shape

        return func, flatfunc

    def _setup_set_funct(self, name):
        """ Sets up our fast set functions."""

        meta = self._vardict[name]

        if meta.get('remote'):
            return _remote_access_error
        elif 'pass_by_obj' in meta and meta['pass_by_obj']:
            return _set_pbo
        else:
            if meta['shape'] == 1:
                return _set_scalar
            else:
                return _set_arr

    def _setup_access_functs(self):
        self._access = {}
        for name in self:
            func, flatfunc = self._setup_get_funct(name)
            setfunc = self._setup_set_funct(name)
            self._access[name] = Accessor(func, setfunc, flatfunc,
                                          self._vardict[name])


class SrcVecWrapper(VecWrapper):
    """ VecWrapper for params and dparams. """

    def setup(self, unknowns_dict, relevance=None, var_of_interest=None,
              store_byobjs=False, shared_vec=None):
        """
        Configure this vector to store a flattened array of the variables
        in unknowns. If store_byobjs is True, then 'pass by object' variables
        will also be stored.

        Args
        ----
        unknowns_dict : dict
            Dictionary of metadata for unknown variables collected from
            components.

        relevance : `Relevance` object
            Object that knows what vars are relevant for each var_of_interest.

        var_of_interest : str or None
            Name of the current variable of interest.

        store_byobjs : bool, optional
            If True, then store 'pass by object' variables.
            By default only 'pass by vector' variables will be stored.

        shared_vec : ndarray, optional
            If not None, create vec as a subslice of this array.
        """

        vec_size = 0
        for meta in itervalues(unknowns_dict):
            promname = meta['promoted_name']
            if relevance is None or relevance.is_relevant(var_of_interest,
                                                          meta['top_promoted_name']):
                vmeta = self._setup_var_meta(meta['pathname'], meta)
                if not vmeta.get('pass_by_obj') and not vmeta.get('remote'):
                    self._slices[promname] = (vec_size, vec_size + vmeta['size'])
                    vec_size += vmeta['size']

                self._vardict[promname] = vmeta

        if shared_vec is not None:
            self.vec = shared_vec[:vec_size]
        else:
            self.vec = numpy.zeros(vec_size)

        # map slices to the array
        for name, meta in iteritems(self):
            if not meta.get('pass_by_obj'):
                if meta.get('remote'):
                    meta['val'] = numpy.array([], dtype=float)
                else:
                    start, end = self._slices[name]
                    meta['val'] = self.vec[start:end]

        # if store_byobjs is True, this is the unknowns vecwrapper,
        # so initialize all of the values from the unknowns dicts.
        if store_byobjs:
            for meta in itervalues(unknowns_dict):
                if 'remote' not in meta and (relevance is None or
                                             relevance.is_relevant(var_of_interest,
                                                                  meta['pathname'])):
                    if meta.get('pass_by_obj'):
                        self._vardict[meta['promoted_name']]['val'].val = meta['val']
                    else:
                        if meta['shape'] == 1:
                            self._vardict[meta['promoted_name']]['val'][0] = meta['val']
                        else:
                            self._vardict[meta['promoted_name']]['val'][:] = meta['val'].flat

        self._setup_prom_map()
        self.setup_flat()
        self._setup_access_functs()

    def _setup_var_meta(self, name, meta):
        """
        Populate the metadata dict for the named variable.

        Args
        ----
        name : str
           The name of the variable to add.

        meta : dict
            Starting metadata for the variable, collected from components
            in an earlier stage of setup.

        """
        vmeta = meta.copy()
        val = meta['val']
        if not is_differentiable(val) or meta.get('pass_by_obj'):
            vmeta['val'] = _ByObjWrapper(val)

        return vmeta

    def _get_flattened_sizes(self):
        """
        Collect all sizes of vars stored in our internal vector.

        Returns
        -------
        list of lists of (name, size) tuples
            A one entry list containing a list of tuples mapping var name to
            local size for 'pass by vector' variables.
        """
        return [[(n, m['size']) for n, m in self._get_vecvars()]]


class TgtVecWrapper(VecWrapper):
    """ Vecwrapper for unknowns, resids, dunknowns, and dresids."""

    def setup(self, parent_params_vec, params_dict, srcvec, my_params,
              connections, relevance=None, var_of_interest=None,
              store_byobjs=False, shared_vec=None):
        """
        Configure this vector to store a flattened array of the variables
        in params_dict. Variable shape and value are retrieved from srcvec.

        Args
        ----
        parent_params_vec : `VecWrapper` or None
            `VecWrapper` of parameters from the parent `System`.

        params_dict : `OrderedDict`
            Dictionary of parameter absolute name mapped to metadata dict.

        srcvec : `VecWrapper`
            Source `VecWrapper` corresponding to the target `VecWrapper` we're building.

        my_params : list of str
            A list of absolute names of parameters that the `VecWrapper` we're building
            will 'own'.

        connections : dict of str : str
            A dict of absolute target names mapped to the absolute name of their
            source variable.

        relevance : `Relevance` object
            Object that knows what vars are relevant for each var_of_interest.

        var_of_interest : str or None
            Name of the current variable of interest.

        store_byobjs : bool, optional
            If True, store 'pass by object' variables in the `VecWrapper` we're building.

        shared_vec : ndarray, optional
            If not None, create vec as a subslice of this array.
        """
        # dparams vector has some additional behavior
        if not store_byobjs:
            self.deriv_units = True

        vec_size = 0
        missing = []  # names of our params that we don't 'own'
        for meta in itervalues(params_dict):
            pathname = meta['pathname']
            if relevance is None or relevance.is_relevant(var_of_interest,
                                                          meta['top_promoted_name']):
                if pathname in my_params:
                    # if connected, get metadata from the source
                    src = connections.get(pathname)
                    if src is None:
                        raise RuntimeError("Parameter '%s' is not connected" % pathname)
                    src_pathname, idxs = src
                    src_rel_name = srcvec.get_promoted_varname(src_pathname)
                    src_meta = srcvec.metadata(src_rel_name)

                    vmeta = self._setup_var_meta(pathname, meta, vec_size,
                                                 src_meta, store_byobjs)
                    vmeta['owned'] = True

                    if not meta.get('remote'):
                        vec_size += vmeta['size']

                    self._vardict[self._scoped_abs_name(pathname)] = vmeta
                else:
                    if parent_params_vec is not None:
                        src = connections.get(pathname)
                        if src:
                            src, idxs = src
                            common = get_common_ancestor(src, pathname)
                            if (common == self._sysdata.pathname or
                                 (self._sysdata.pathname+'.') not in common):
                                missing.append(meta)

        if shared_vec is not None:
            self.vec = shared_vec[:vec_size]
        else:
            self.vec = numpy.zeros(vec_size)

        # map slices to the array
        for name, meta in iteritems(self._vardict):
            if not meta.get('pass_by_obj') and not meta.get('remote'):
                start, end = self._slices[name]
                meta['val'] = self.vec[start:end]

        # fill entries for missing params with views from the parent
        for meta in missing:
            pathname = meta['pathname']
            newmeta = parent_params_vec._vardict[parent_params_vec._scoped_abs_name(pathname)]
            if newmeta['pathname'] == pathname:
                newmeta = newmeta.copy()
                newmeta['promoted_name'] = meta['promoted_name']
                newmeta['owned'] = False # mark this param as not 'owned' by this VW
                self._vardict[self._scoped_abs_name(pathname)] = newmeta

        # Finally, set up unit conversions, if any exist.
        for meta in itervalues(params_dict):
            pathname = meta['pathname']
            if pathname in my_params and (relevance is None or
                                          relevance.is_relevant(var_of_interest,
                                                                pathname)):
                unitconv = meta.get('unit_conv')
                if unitconv:
                    scale, offset = unitconv
                    if self.deriv_units:
                        offset = 0.0
                    self._vardict[self._scoped_abs_name(pathname)]['unit_conv'] = (scale, offset)

        self._setup_prom_map()
        self.setup_flat()
        self._setup_access_functs()

    def _setup_var_meta(self, pathname, meta, index, src_meta, store_byobjs):
        """
        Populate the metadata dict for the named variable.

        Args
        ----
        pathname : str
            Absolute name of the variable.

        meta : dict
            Metadata for the variable collected from components.

        index : int
            Index into the array where the variable value is to be stored
            (if variable is not 'pass by object').

        src_meta : dict
            Metadata for the source variable that this target variable is
            connected to.

        store_byobjs : bool, optional
            If True, store 'pass by object' variables in the `VecWrapper`
            we're building.
        """
        vmeta = meta.copy()
        if 'src_indices' not in vmeta and 'src_indices' not in src_meta:
            vmeta['size'] = src_meta['size']

        if src_meta.get('pass_by_obj'):
            if not meta.get('remote') and store_byobjs:
                vmeta['val'] = src_meta['val']
            vmeta['pass_by_obj'] = True
        elif not vmeta.get('remote'):
            self._slices[self._scoped_abs_name(pathname)] = (index, index + vmeta['size'])

        return vmeta

    def _add_unconnected_var(self, pathname, meta):
        """
        Add an entry to this vecwrapper for the given unconnected variable so the
        component can access its value through the vecwrapper.
        """
        sname = self._scoped_abs_name(pathname)
        vmeta = meta.copy()
        if 'val' in meta:
            val = meta['val']
        elif 'shape' in meta:
            shape = meta['shape']
            val = numpy.zeros(shape)
        else:
            raise RuntimeError("Unconnected param '%s' has no specified val or shape" %
                               pathname)

        if not vmeta.get('pass_by_obj'):
            if isinstance(val, numpy.ndarray):
                self.flat[sname] = val.flat
            else:
                self.flat[sname] = numpy.array([val])

        vmeta['val'] = _ByObjWrapper(val)
        vmeta['pass_by_obj'] = True
        self._vardict[sname] = vmeta
        func, flatfunc = self._setup_get_funct(sname)
        self._access[sname] = Accessor(func, self._setup_set_funct(sname),
                                       flatfunc, self._vardict[sname])

    def _get_flattened_sizes(self):
        """
        Returns
        -------
        list of lists of tuples of the form (name, size)
            A one entry list of lists with tuples pairing names to local sizes
            of owned, local params in this `VecWrapper`.
        """
        return [[(n, m['size']) for n, m in self._get_vecvars()
                    if m.get('owned')]]

    def _apply_unit_derivatives(self):
        """ Applies derivative of the unit conversion factor to params
        sitting in vector.
        """
        if self.deriv_units:
            for name, meta in iteritems(self._vardict):
                if 'unit_conv' in meta:
                    meta['val'] *= meta['unit_conv'][0]

    # def _apply_units(self):
    #     """ Applies the unit conversion factor to params
    #     sitting in vector.
    #     """
    #     for name, meta in iteritems(self._vardict):
    #         if 'unit_conv' in meta and 'owned' in meta:
    #             scale, offset = meta['unit_conv']
    #             val = meta['val']
    #             if offset != 0.0:
    #                 val += offset
    #             val *= scale

class _PlaceholderVecWrapper(object):
    """
    A placeholder for a dict-like container of a collection of variables.

    Args
    ----
    name : str
        the name of the vector
    """

    def __init__(self, name=''):
        self.name = name

    def __getitem__(self, name):
        """
        Retrieve unflattened value of named var. Since this is just a
        placeholder, will raise an exception stating that setup() has
        not been called yet.

        Args
        ----
        name : str
            Name of variable to get the value for.

        Raises
        ------
        AttributeError
        """
        raise AttributeError("'%s' has not been initialized, "
                             "setup() must be called before '%s' can be accessed" %
                             (self.name, name))

    def __contains__(self, name):
        self.__getitem__(name)

    def __setitem__(self, name, value):
        """
        Set the value of the named variable. Since this is just a
        placeholder, will raise an exception stating that setup() has
        not been called yet.

        Args
        ----
        name : str
            Name of variable to get the value for.

        value :
            The unflattened value of the named variable.

        Raises
        ------
        AttributeError
        """
        raise AttributeError("'%s' has not been initialized, "
                             "setup() must be called before '%s' can be accessed" %
                             (self.name, name))


# accessor functions
def _get_pbo(meta):
    """pass by obj"""
    return meta['val'].val

def _get_arr(meta):
    """Array with same shape"""
    return meta['val']

def _get_arr_diff_shape(meta):
    """Array with different shape"""
    return meta['val'].reshape(meta['shape'])

def _get_scalar(meta):
    return meta['val'][0]

def _get_arr_units(meta):
    """Array with same shape and unit conversion"""
    scale, offset = meta['unit_conv']
    vec = meta['val'] + offset
    vec *= scale
    return vec

def _get_arr_units_diff_shape(meta):
    """Array with diff shape and unit conversion"""
    scale, offset = meta['unit_conv']
    vec = meta['val'] + offset
    vec *= scale
    return vec.reshape(meta['shape'])

def _get_scalar_units(meta):
    scale, offset = meta['unit_conv']
    return scale*(meta['val'][0] + offset)

def _set_arr(meta, value):
    meta['val'][:] = value.flat

def _set_scalar(meta, value):
    meta['val'][0] = value

def _set_pbo(meta, value):
    meta['val'].val = value

def _remote_access_error(meta, value=None):
    msg = "Cannot access remote Variable '{name}' in this process."
    raise RuntimeError(msg.format(name=meta['promoted_name']))
