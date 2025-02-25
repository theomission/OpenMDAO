""" Test for the Component class"""

import unittest
from six import text_type, PY3

if PY3:
    def py3fix(s):
        return s.replace('<type', '<class')
else:
    def py3fix(s):
        return s

import numpy as np

from openmdao.api import Component, Problem

class TestComponent(unittest.TestCase):

    def setUp(self):
        self.comp = Component()

    def test_not_impl(self):
        with self.assertRaises(RuntimeError) as cm:
            self.comp.solve_nonlinear({}, {}, {})

        expected_msg = py3fix("Class 'Component' does not implement 'solve_nonlinear'")
        self.assertEqual(str(cm.exception), expected_msg)

    def test_param_name_errors(self):
        self.comp.add_param("xxyyzz", 0.0)

        with self.assertRaises(RuntimeError) as cm:
            self.comp.add_param("xxyyzz", 0.0)

        expected_msg = py3fix(": variable 'xxyyzz' already exists.")
        self.assertEqual(str(cm.exception), expected_msg)

        with self.assertRaises(NameError) as cm:
            self.comp.add_param("xx/yy/zz", 0.0)

        expected_msg = py3fix(": 'xx/yy/zz' is not a valid variable name.")
        self.assertEqual(str(cm.exception), expected_msg)

        self.comp._setup_variables()

        with self.assertRaises(RuntimeError) as cm:
            self.comp.add_param("latefortheparty", 0.0)

        expected_msg = ": can't add variable 'latefortheparty' because setup has already been called."
        self.assertEqual(str(cm.exception), expected_msg)

    def test_output_name_errors(self):
        self.comp.add_output("xxyyzz", 0.0)

        with self.assertRaises(RuntimeError) as cm:
            self.comp.add_output("xxyyzz", 0.0)

        expected_msg = py3fix(": variable 'xxyyzz' already exists.")
        self.assertEqual(str(cm.exception), expected_msg)

        with self.assertRaises(NameError) as cm:
            self.comp.add_output("xx/yy/zz", 0.0)

        expected_msg = py3fix(": 'xx/yy/zz' is not a valid variable name.")
        self.assertEqual(str(cm.exception), expected_msg)

        self.comp._setup_variables()

        with self.assertRaises(RuntimeError) as cm:
            self.comp.add_output("latefortheparty", 0.0)

        expected_msg = ": can't add variable 'latefortheparty' because setup has already been called."
        self.assertEqual(str(cm.exception), expected_msg)

    def test_promotes(self):
        self.comp.add_param("xxyyzz", 0.0)
        self.comp.add_param("foobar", 0.0)
        self.comp.add_output("a:bcd:efg", -1)
        self.comp.add_output("x_y_z", np.zeros(10))

        self.comp._promotes = ('*',)
        for name in self.comp._params_dict:
            self.assertTrue(self.comp._promoted(name))
        for name in self.comp._unknowns_dict:
            self.assertTrue(self.comp._promoted(name))

        self.assertFalse(self.comp._promoted('blah'))

        self.comp._promotes = ('x*',)
        for name in self.comp._params_dict:
            if name.startswith('x'):
                self.assertTrue(self.comp._promoted(name))
            else:
                self.assertFalse(self.comp._promoted(name))
        for name in self.comp._unknowns_dict:
            if name.startswith('x'):
                self.assertTrue(self.comp._promoted(name))
            else:
                self.assertFalse(self.comp._promoted(name))

        self.comp._promotes = ('*:efg',)
        for name in self.comp._params_dict:
            if name.endswith(':efg'):
                self.assertTrue(self.comp._promoted(name))
            else:
                self.assertFalse(self.comp._promoted(name))
        for name in self.comp._unknowns_dict:
            if name.endswith(':efg'):
                self.assertTrue(self.comp._promoted(name))
            else:
                self.assertFalse(self.comp._promoted(name))
        # catch bad type on _promotes
        try:
            self.comp._promotes = ('*')
            self.comp._promoted('xxyyzz')
        except Exception as err:
            self.assertEqual(text_type(err),
                             "'' promotes must be specified as a list, tuple or other iterator of strings, but '*' was specified")

    def test_add_params(self):
        self.comp.add_param("x", 0.0)
        self.comp.add_param("y", 0.0)
        self.comp.add_param("z", shape=(1,))
        self.comp.add_param("t", shape=2)
        self.comp.add_param("u", shape=1)

        with self.assertRaises(ValueError) as cm:
            self.comp.add_param("w")

        self.assertEqual(str(cm.exception), "Shape of param 'w' must be specified because 'val' is not set")

        prob = Problem()
        self.comp._init_sys_data('', prob._probdata)
        params, unknowns = self.comp._setup_variables()

        self.assertEqual(["x", "y", "z", "t", "u"], list(params.keys()))

        self.assertEqual(params["x"], {'shape': 1, 'promoted_name': 'x', 'pathname': 'x', 'val': 0.0, 'size': 1})
        self.assertEqual(params["y"], {'shape': 1, 'promoted_name': 'y', 'pathname': 'y', 'val': 0.0, 'size': 1})
        np.testing.assert_array_equal(params["z"]["val"], np.zeros((1,)))
        np.testing.assert_array_equal(params["t"]["val"], np.zeros((2,)))
        self.assertEqual(params["u"], {'shape': 1, 'promoted_name': 'u', 'pathname': 'u', 'val': 0.0, 'size': 1})

    def test_add_outputs(self):
        self.comp.add_output("x", -1)
        self.comp.add_output("y", np.zeros(10))
        self.comp.add_output("z", shape=(10,))
        self.comp.add_output("t", shape=2)
        self.comp.add_output("u", shape=1)

        with self.assertRaises(ValueError) as cm:
            self.comp.add_output("w")

        self.assertEqual(str(cm.exception), "Shape of output 'w' must be specified because 'val' is not set")

        prob = Problem()
        self.comp._init_sys_data('', prob._probdata)
        params, unknowns = self.comp._setup_variables()

        self.assertEqual(["x", "y", "z", "t", "u"], list(unknowns.keys()))

        self.assertIsInstance(unknowns["x"]["val"], int)
        self.assertIsInstance(unknowns["y"]["val"], np.ndarray)
        self.assertIsInstance(unknowns["z"]["val"], np.ndarray)
        self.assertIsInstance(unknowns["t"]["val"], np.ndarray)
        self.assertIsInstance(unknowns["u"]["val"], float)

        self.assertEqual(unknowns["x"], {'pass_by_obj': True, 'promoted_name': 'x', 'pathname': 'x', 'val': -1, 'size': 0})
        self.assertEqual(list(unknowns["y"]["val"]), 10*[0])
        np.testing.assert_array_equal(unknowns["z"]["val"], np.zeros((10,)))
        np.testing.assert_array_equal(unknowns["t"]["val"], np.zeros((2,)))
        self.assertEqual(unknowns["u"], {'shape': 1, 'promoted_name': 'u', 'pathname': 'u', 'val': 0.0, 'size': 1})

    def test_add_states(self):
        self.comp.add_state("s1", 0.0)
        self.comp.add_state("s2", 6.0)
        self.comp.add_state("s3", shape=(1, ))
        self.comp.add_state("s4", shape=2)
        self.comp.add_state("s5", shape=1)

        with self.assertRaises(ValueError) as cm:
            self.comp.add_state("s6")

        self.assertEqual(str(cm.exception), "Shape of state 's6' must be specified because 'val' is not set")

        prob = Problem()
        self.comp._init_sys_data('', prob._probdata)
        params, unknowns = self.comp._setup_variables()

        self.assertEqual(["s1", "s2", "s3", "s4", "s5"], list(unknowns.keys()))

        self.assertEqual(unknowns["s1"], {'val': 0.0, 'state': True, 'shape': 1, 'pathname': 's1', 'promoted_name': 's1', 'size': 1})
        self.assertEqual(unknowns["s2"], {'val': 6.0, 'state': True, 'shape': 1, 'pathname': 's2', 'promoted_name': 's2', 'size': 1})
        np.testing.assert_array_equal(unknowns["s3"]["val"], np.zeros((1,)))
        np.testing.assert_array_equal(unknowns["s4"]["val"], np.zeros((2,)))
        self.assertEqual(unknowns["s5"], {'val': 0.0, 'state': True, 'shape': 1, 'pathname': 's5', 'promoted_name': 's5', 'size': 1})

    def test_variable_access(self):
        self.comp.add_output("x_y_z", np.zeros(10))

        try:
            self.comp["x_y_z"]
        except Exception as err:
            self.assertEqual(str(err),
                             "Variable 'x_y_z' must be accessed from a containing Group")
        else:
            self.fail("Exception expected")

    def test_generate_numpydocstring(self):
        self.comp.add_param("x", 0.0)
        self.comp.add_param("y", shape=2)
        self.comp.add_output("z", -1)
        self.comp.add_state("s", 0.0)
        test_string = self.comp.generate_docstring()
        original_string = '    """\n\n    Params\n    ----------\n    x: param ({\'promoted_name\': x, \'shape\': 1, \'size\': 1, \'val\': 0.0})\n    y: param ({\'promoted_name\': y, \'shape\': (2,), \'size\': 2, \'val\': [ 0.  0.]})\n    z : unknown ({\'pass_by_obj\': True, \'promoted_name\': z, \'size\': 0, \'val\': -1})\n    s : unknown ({\'promoted_name\': s, \'shape\': 1, \'size\': 1, \'state\': True, \'val\': 0.0})\n\n    Options\n    -------\n    fd_options[\'force_fd\'] :  bool(False)\n        Set to True to finite difference this system.\n    fd_options[\'form\'] :  str(\'forward\')\n        Finite difference mode. (forward, backward, central) You can also set to \'complex_step\' to peform the complex step method if your components support it.\n    fd_options[\'step_size\'] :  float(1e-06)\n        Default finite difference stepsize\n    fd_options[\'step_type\'] :  str(\'absolute\')\n        Set to absolute, relative\n\n    """\n'
        self.assertEqual(original_string, test_string)

if __name__ == "__main__":
    unittest.main()
