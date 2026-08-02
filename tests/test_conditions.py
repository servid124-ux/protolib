import unittest

from protolib.conditions import eval_condition
from protolib.errors import ConditionError


class TestOperators(unittest.TestCase):
    def setUp(self):
        self.fields = {"x": 1, "y": 0, "flag": True, "name": "identification"}

    def test_strict_equality(self):
        self.assertTrue(eval_condition("fields.x === 1", self.fields))
        self.assertFalse(eval_condition("fields.x === 2", self.fields))

    def test_strict_inequality(self):
        self.assertTrue(eval_condition("fields.y !== 1", self.fields))

    def test_loose_equality_coerces_numeric_string(self):
        # switch/mapper keys often arrive as strings ("0", "0x1f"); the
        # loose '==' is specifically meant to bridge that, per conditions.py.
        self.assertTrue(eval_condition("fields.x == '1'", self.fields))
        self.assertFalse(eval_condition("fields.x == '2'", self.fields))

    def test_loose_equality_bool_vs_numeric_string_is_not_coerced(self):
        # True == 1 is True natively (bool is an int subclass in Python,
        # and JS agrees: `true == 1`). What conditions.py's bool guard in
        # _loose_eq specifically blocks is coercing a bool against a
        # numeric *string* the way it does for real ints (fields.x == '1').
        self.assertTrue(eval_condition("fields.flag == true", self.fields))
        self.assertTrue(eval_condition("fields.flag == 1", self.fields))
        self.assertFalse(eval_condition("fields.flag == '1'", self.fields))

    def test_comparison_operators(self):
        self.assertTrue(eval_condition("fields.x >= 1", self.fields))
        self.assertTrue(eval_condition("fields.x <= 1", self.fields))
        self.assertTrue(eval_condition("fields.x > 0", self.fields))
        self.assertTrue(eval_condition("fields.x < 2", self.fields))

    def test_and_or(self):
        self.assertTrue(eval_condition("fields.x === 1 && fields.y === 0", self.fields))
        self.assertFalse(eval_condition("fields.x === 9 && fields.y === 0", self.fields))
        self.assertTrue(eval_condition("fields.x === 9 || fields.y === 0", self.fields))

    def test_parentheses(self):
        self.assertTrue(eval_condition("(fields.x > 0) || (fields.y > 0)", self.fields))

    def test_truthiness_of_single_operand(self):
        self.assertTrue(eval_condition("fields.flag", self.fields))
        self.assertFalse(eval_condition("fields.y", self.fields))  # 0 is falsy

    def test_string_literal_comparison(self):
        self.assertTrue(eval_condition("fields.name === 'identification'", self.fields))


class TestPaths(unittest.TestCase):
    def test_root_and_parent_refs(self):
        root = {"version": 47}
        parent = {"hasData": True}
        self.assertTrue(eval_condition("$root.version >= 47", {}, root, parent))
        self.assertTrue(eval_condition("$parent.hasData", {}, root, parent))

    def test_index_access(self):
        fields = {"items": ["a", "b", "c"]}
        self.assertTrue(eval_condition("fields.items[1] === 'b'", fields))

    def test_missing_path_resolves_to_none_not_error(self):
        self.assertFalse(eval_condition("fields.doesNotExist === 1", {"other": 1}))


class TestMalformedExpressions(unittest.TestCase):
    def test_unbalanced_parens_raises_condition_error(self):
        # README section 12 documents malformed conditions as ConditionError.
        # (Previously the parser let a bare ValueError escape instead --
        # see the fix in protolib/conditions.py::eval_condition.)
        with self.assertRaises(ConditionError):
            eval_condition("(fields.x === 1", {"x": 1})

    def test_trailing_garbage_raises_condition_error(self):
        with self.assertRaises(ConditionError):
            eval_condition("fields.x === 1 )", {"x": 1})

    def test_empty_expression_raises_condition_error(self):
        with self.assertRaises(ConditionError):
            eval_condition("", {"x": 1})


if __name__ == "__main__":
    unittest.main()
