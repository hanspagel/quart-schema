from dataclasses import dataclass
from typing import Type, Union

import pytest
from attrs import define
from msgspec import Struct
from pydantic import BaseModel
from pydantic.dataclasses import dataclass as pydantic_dataclass

from quart_schema.conversion import convert_headers, model_dump, model_load, model_schema
from .helpers import ADetails, DCDetails, MDetails, PyDCDetails, PyDetails


class ValidationError(Exception):
    pass


@pytest.mark.parametrize("type_", [ADetails, DCDetails, MDetails, PyDetails, PyDCDetails])
def test_model_dump(
    type_: Type[Union[ADetails, DCDetails, MDetails, PyDetails, PyDCDetails]]
) -> None:
    assert model_dump(type_(name="bob", age=2), by_alias=False) == {  # type: ignore
        "name": "bob",
        "age": 2,
    }


@pytest.mark.parametrize(
    "type_, preference",
    [
        (ADetails, "msgspec"),
        (DCDetails, "msgspec"),
        (DCDetails, "pydantic"),
        (MDetails, "msgspec"),
        (PyDetails, "pydantic"),
        (PyDCDetails, "pydantic"),
    ],
)
def test_model_dump_list(
    type_: Type[Union[ADetails, DCDetails, MDetails, PyDetails, PyDCDetails]],
    preference: str,
) -> None:
    assert model_dump(
        [type_(name="bob", age=2), type_(name="jim", age=3)], by_alias=False, preference=preference
    ) == [{"name": "bob", "age": 2}, {"name": "jim", "age": 3}]


@pytest.mark.parametrize("type_", [ADetails, DCDetails, MDetails, PyDetails, PyDCDetails])
def test_model_load(
    type_: Type[Union[ADetails, DCDetails, MDetails, PyDetails, PyDCDetails]]
) -> None:
    assert model_load({"name": "bob", "age": 2}, type_, exception_class=ValidationError) == type_(
        name="bob", age=2
    )


@pytest.mark.parametrize("type_", [ADetails, DCDetails, MDetails, PyDetails, PyDCDetails])
def test_model_load_error(
    type_: Type[Union[ADetails, DCDetails, MDetails, PyDetails, PyDCDetails]]
) -> None:
    with pytest.raises(ValidationError):
        model_load({"name": "bob", "age": "two"}, type_, exception_class=ValidationError)


@pytest.mark.parametrize("type_", [ADetails, DCDetails, MDetails])
def test_model_schema_msgspec(type_: Type[Union[ADetails, DCDetails, MDetails]]) -> None:
    assert model_schema(type_, preference="msgspec") == {
        "title": type_.__name__,
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
        },
        "required": ["name"],
    }


@pytest.mark.parametrize("type_", [DCDetails, PyDetails, PyDCDetails])
def test_model_schema_pydantic(type_: Type[Union[DCDetails, PyDetails, PyDCDetails]]) -> None:
    assert model_schema(type_, preference="pydantic") == {
        "properties": {
            "name": {"title": "Name", "type": "string"},
            "age": {
                "anyOf": [{"type": "integer"}, {"type": "null"}],
                "default": None,
                "title": "Age",
            },
        },
        "required": ["name"],
        "title": type_.__name__,
        "type": "object",
    }


@define
class AHeaders:
    x_info: str


class MHeaders(Struct):
    x_info: str


@dataclass
class DCHeaders:
    x_info: str


class PyHeaders(BaseModel):
    x_info: str


@pydantic_dataclass
class PyDCHeaders:
    x_info: str


@pytest.mark.parametrize("type_", [AHeaders, DCHeaders, MHeaders, PyHeaders, PyDCHeaders])
def test_convert_headers(
    type_: Type[Union[AHeaders, DCHeaders, MHeaders, PyHeaders, PyDCHeaders]],
) -> None:
    convert_headers(
        {
            "X-Info": "ABC",
            "Other": "2",
        },
        type_,
        exception_class=ValidationError,
    ) == type_(x_info="ABC")
