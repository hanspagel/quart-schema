from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Optional, Type, TypeVar, Union

import humps
from quart import current_app
from quart.typing import HeadersValue, ResponseReturnValue as QuartResponseReturnValue, StatusCode
from werkzeug.datastructures import Headers
from werkzeug.exceptions import HTTPException

from .typing import Model, ResponseReturnValue, ResponseValue

try:
    from pydantic import (
        BaseModel,
        RootModel,
        TypeAdapter,
        ValidationError as PydanticValidationError,
    )
    from pydantic.dataclasses import is_pydantic_dataclass
except ImportError:
    PYDANTIC_INSTALLED = False

    class BaseModel:  # type: ignore
        pass

    class RootModel:  # type: ignore
        pass

    class TypeAdapter:  # type: ignore
        pass

    def is_pydantic_dataclass(object_: Any) -> bool:  # type: ignore
        return False

    class PydanticValidationError(Exception):  # type: ignore
        pass

else:
    PYDANTIC_INSTALLED = True


try:
    from attrs import fields as attrs_fields, has as is_attrs
    from msgspec import convert, Struct, to_builtins, ValidationError as MsgSpecValidationError
    from msgspec.json import schema_components
except ImportError:
    MSGSPEC_INSTALLED = False

    class Struct:  # type: ignore
        pass

    def is_attrs(object_: Any) -> bool:  # type: ignore
        return False

    def convert(object_: Any, type_: Any) -> Any:  # type: ignore
        raise RuntimeError("Cannot convert, msgspec not installed")

    def to_builtins(object_: Any) -> Any:  # type: ignore
        return object_

    class MsgSpecValidationError(Exception):  # type: ignore
        pass

else:
    MSGSPEC_INSTALLED = True


PYDANTIC_REF_TEMPLATE = "#/components/schemas/{model}"
MSGSPEC_REF_TEMPLATE = "#/components/schemas/{name}"

T = TypeVar("T", bound=Model)


def convert_response_return_value(
    result: ResponseReturnValue | HTTPException,
) -> QuartResponseReturnValue | HTTPException:
    value: ResponseValue
    headers: Optional[HeadersValue] = None
    status: Optional[StatusCode] = None

    if isinstance(result, HTTPException):
        return result
    elif isinstance(result, tuple):
        if len(result) == 3:
            value, status, headers = result  # type: ignore
        elif len(result) == 2:
            value, status_or_headers = result
            if isinstance(status_or_headers, int):
                status = status_or_headers
            else:
                headers = status_or_headers  # type: ignore
    else:
        value = result

    value = model_dump(
        value,
        camelize=current_app.config["QUART_SCHEMA_CONVERT_CASING"],
        by_alias=current_app.config["QUART_SCHEMA_BY_ALIAS"],
        preference=current_app.config["QUART_SCHEMA_CONVERSION_PREFERENCE"],
    )
    headers = model_dump(
        headers,  # type: ignore
        kebabize=True,
        by_alias=current_app.config["QUART_SCHEMA_BY_ALIAS"],
        preference=current_app.config["QUART_SCHEMA_CONVERSION_PREFERENCE"],
    )

    new_result: ResponseReturnValue
    if isinstance(result, tuple):
        if len(result) == 3:
            new_result = value, status, headers
        elif len(result) == 2:
            if status is not None:
                new_result = value, status
            else:
                new_result = value, headers
    else:
        new_result = value

    return new_result


def model_dump(
    raw: ResponseValue,
    *,
    by_alias: bool,
    camelize: bool = False,
    kebabize: bool = False,
    preference: Optional[str] = None,
) -> dict | list:
    if is_pydantic_dataclass(raw):  # type: ignore
        value = RootModel[type(raw)](raw).model_dump()  # type: ignore
    elif isinstance(raw, BaseModel):
        value = raw.model_dump(by_alias=by_alias)
    elif isinstance(raw, Struct) or is_attrs(raw):  # type: ignore
        value = to_builtins(raw)
    elif (
        (isinstance(raw, (list, dict)) or is_dataclass(raw))
        and PYDANTIC_INSTALLED
        and preference != "msgspec"
    ):
        value = TypeAdapter(type(raw)).dump_python(raw)
    elif (
        (isinstance(raw, (list, dict)) or is_dataclass(raw))
        and MSGSPEC_INSTALLED
        and preference != "pydantic"
    ):
        value = to_builtins(raw)
    else:
        return raw  # type: ignore

    if camelize:
        return humps.camelize(value)
    elif kebabize:
        return humps.kebabize(value)
    else:
        return value


def model_load(
    data: dict,
    model_class: Type[T],
    exception_class: Type[Exception],
    *,
    decamelize: bool = False,
    preference: Optional[str] = None,
) -> T:
    if decamelize:
        data = humps.decamelize(data)

    try:
        if (
            is_pydantic_dataclass(model_class)
            or issubclass(model_class, BaseModel)
            or (
                (isinstance(model_class, (list, dict)) or is_dataclass(model_class))
                and PYDANTIC_INSTALLED
                and preference != "msgspec"
            )
        ):
            return TypeAdapter(model_class).validate_python(data)  # type: ignore
        elif (
            issubclass(model_class, Struct)
            or is_attrs(model_class)
            or (
                (isinstance(model_class, (list, dict)) or is_dataclass(model_class))
                and MSGSPEC_INSTALLED
                and preference != "pydantic"
            )
        ):
            return convert(data, model_class, strict=False)  # type: ignore
        else:
            raise TypeError(f"Cannot load {model_class}")
    except (TypeError, MsgSpecValidationError, PydanticValidationError, ValueError) as error:
        raise exception_class(error)


def model_schema(model_class: Type[Model], *, preference: Optional[str] = None) -> dict:
    if (
        is_pydantic_dataclass(model_class)
        or issubclass(model_class, BaseModel)
        or (isinstance(model_class, (list, dict)) and preference != "msgspec")
        or (is_dataclass(model_class) and preference != "msgspec")
    ):
        return TypeAdapter(model_class).json_schema(ref_template=PYDANTIC_REF_TEMPLATE)
    elif (
        issubclass(model_class, Struct)
        or is_attrs(model_class)
        or (isinstance(model_class, (list, dict)) and preference != "pydantic")
        or (is_dataclass(model_class) and preference != "pydantic")
    ):
        _, schema = schema_components([model_class], ref_template=MSGSPEC_REF_TEMPLATE)
        return list(schema.values())[0]
    else:
        raise TypeError(f"Cannot create schema for {model_class}")


def convert_headers(
    raw: Union[Headers, dict], model_class: Type[T], exception_class: Type[Exception]
) -> T:
    if is_pydantic_dataclass(model_class):
        fields_ = set(model_class.__pydantic_fields__.keys())
    elif is_dataclass(model_class):
        fields_ = {field.name for field in fields(model_class)}
    elif issubclass(model_class, BaseModel):
        fields_ = set(model_class.model_fields.keys())
    elif is_attrs(model_class):
        fields_ = {field.name for field in attrs_fields(model_class)}
    elif issubclass(model_class, Struct):
        fields_ = set(model_class.__struct_fields__)
    else:
        raise TypeError(f"Cannot convert to {model_class}")

    result = {}
    for raw_key in raw.keys():
        key = humps.dekebabize(raw_key).lower()
        if key in fields_:
            if isinstance(raw, Headers):
                result[key] = ",".join(raw.get_all(raw_key))
            else:
                result[key] = raw[raw_key]

    try:
        return model_class(**result)
    except (TypeError, MsgSpecValidationError, ValueError) as error:
        raise exception_class(error)
