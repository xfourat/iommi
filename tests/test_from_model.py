import pytest
from django.db.models import (
    Model,
    CharField,
)

from iommi.from_model import (
    get_name_field_for_model,
    NoRegisteredNameException,
    register_name_field,
    create_members_from_model,
)


def test_get_name_field_for_model_error():
    class NoRegisteredNameExceptionModel(Model):
        pass

    with pytest.raises(NoRegisteredNameException) as e:
        get_name_field_for_model(NoRegisteredNameExceptionModel)

    print(e.value)
    assert str(e.value) == "The model <class 'tests.test_from_model.test_get_name_field_for_model_error.<locals>.NoRegisteredNameExceptionModel'> has no registered name field. Please register one with register_name_field."


def test_register_name_field_error():
    class RegisterNameExceptionModel(Model):
        foo = CharField(max_length=100)

    with pytest.raises(Exception) as e:
        register_name_field(model=RegisterNameExceptionModel, name_field='foo')

    print(e.value)
    assert str(e.value) == """Cannot register name "foo" for model <class 'tests.test_from_model.test_register_name_field_error.<locals>.RegisterNameExceptionModel'>. foo must be unique."""


def test_create_members_from_model_error_overlapping_keys_include():
    with pytest.raises(AssertionError) as e:
        create_members_from_model(
            default_factory=None,
            model=None,
            member_params_by_member_name=None,
            include=['a'],
            additional=dict(a=None),
        )

    assert str(e.value) == 'additional contains a which conflicts with the same name in include.'


def test_create_members_from_model_error_overlapping_keys_exclude():
    with pytest.raises(AssertionError) as e:
        create_members_from_model(
            default_factory=None,
            model=None,
            member_params_by_member_name=None,
            exclude=['a'],
            additional=dict(a=None),
        )

    assert str(e.value) == 'additional contains a which conflicts with the same name in exclude.'