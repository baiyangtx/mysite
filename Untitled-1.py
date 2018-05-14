#!/usr/bin/env python
# -*- coding: utf-8 -*-

from numbers import Number
from unittest import TestCase, main

from jsonschema import validators, Draft4Validator, FormatChecker
from jsonschema.exceptions import ValidationError


class TestCustomTypeValidatorFormat(TestCase):
    """Tests support for combination of custom type, validator and format."""

    def test_combination(self):
        # Define JSON schema
        schema = {
            'type': 'object',
            'properties': {
                'value': {
                    'type': 'float',
                    'format': 'even',
                    'is_positive': True
                },
            }
        }

        # Define custom validators. Each must take exactly 4 arguments as below.
        def is_positive(validator, value, instance, schema):
            if not isinstance(instance, Number):
                yield ValidationError("%r is not a number" % instance)

            if value and instance <= 0:
                yield ValidationError("%r is not positive integer" % (instance,))
            elif not value and instance > 0:
                yield ValidationError("%r is not negative integer nor zero" % (instance,))

        # Add your custom validators among existing ones.
        all_validators = dict(Draft4Validator.VALIDATORS)
        all_validators['is_positive'] = is_positive

        # Create a new validator class. It will use your new validators and the schema
        # defined above.
        MyValidator = validators.create(
            meta_schema=Draft4Validator.META_SCHEMA,
            validators=all_validators
        )

        # Create a new format checker instance.
        format_checker = FormatChecker()

        # Register a new format checker method for format 'even'. It must take exactly one
        # argument - the value for checking.
        @format_checker.checks('even')
        def even_number(value):
            return value % 2 == 0

        # Create a new instance of your custom validator. Add a custom type.
        my_validator = MyValidator(
            schema, types={"float": float}, format_checker=format_checker
        )

        # Now you can use your fully customized JSON schema validator.

        # Positive but not even
        self.assertRaises(ValidationError, my_validator.validate, {'value': 1})

        # Positive and even but not float
        self.assertRaises(ValidationError, my_validator.validate, {'value': 2})

        # Positive and float but not even
        self.assertRaises(ValidationError, my_validator.validate, {'value': 3.0})

        # Float and even but not positive
        self.assertRaises(ValidationError, my_validator.validate, {'value': -2.0})

        # Even, but not positive nor float
        self.assertRaises(ValidationError, my_validator.validate, {'value': -2})

        # Positive, float, and even
        self.assertIsNone(my_validator.validate({'value': 4.0}))


if __name__ == '__main__':
    main()