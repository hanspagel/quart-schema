from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from types import new_class
from typing import Any, cast, Dict, Optional, overload, Tuple, Type, Union

from pydantic import ValidationError
from pydantic.json import pydantic_encoder
from quart import Quart, render_template_string
from quart.json import JSONEncoder as QuartJSONEncoder
from quart.wrappers import Websocket

from .typing import BM, DC, WebsocketProtocol
from .validation import (
    QUART_SCHEMA_QUERYSTRING_ATTRIBUTE,
    QUART_SCHEMA_REQUEST_ATTRIBUTE,
    QUART_SCHEMA_RESPONSE_ATTRIBUTE,
)

PATH_RE = re.compile("<(?:[^:]*:)?([^>]+)>")

REDOC_TEMPLATE = """
<head>
  <title>{{ title }}</title>
  <style>
    body {
      margin: 0;
      padding: 0;
    }
  </style>
</head>
<body>
  <redoc spec-url="{{ openapi_path }}"></redoc>
  <script src="{{ redoc_js_url }}"></script>
</body>
"""

SWAGGER_TEMPLATE = """
<head>
  <link type="text/css" rel="stylesheet" href="{{ swagger_css_url }}">
  <title>{{ title }}</title>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{{ swagger_js_url }}"></script>
  <script>
    const ui = SwaggerUIBundle({
      deepLinking: true,
      dom_id: "#swagger-ui",
      layout: "BaseLayout",
      presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
      ],
      showExtensions: true,
      showCommonExtensions: true,
      url: "{{ openapi_path }}"
    });
  </script>
</body>
"""


class SchemaValidationError(Exception):
    pass


class WebsocketMixin:
    @overload
    async def receive_as(self: WebsocketProtocol, model_class: Type[BM]) -> BM:
        ...

    @overload
    async def receive_as(self: WebsocketProtocol, model_class: Type[DC]) -> DC:
        ...

    async def receive_as(
        self: WebsocketProtocol, model_class: Union[Type[BM], Type[DC]]
    ) -> Union[BM, DC]:
        data = await self.receive_json()
        try:
            return model_class(**data)
        except ValidationError:
            raise SchemaValidationError()

    async def send_as(
        self: WebsocketProtocol, value: Any, model_class: Union[Type[BM], Type[DC]]
    ) -> None:
        if isinstance(value, dict):
            try:
                model_value = model_class(**value)
            except ValidationError:
                raise SchemaValidationError()
        elif type(value) == model_class:
            model_value = value
        else:
            raise SchemaValidationError()
        if is_dataclass(model_value):
            data = asdict(model_value)
        else:
            model_value = cast(BM, model_value)
            data = model_value.dict()
        await self.send_json(data)


class JSONEncoder(QuartJSONEncoder):
    def default(self, object_: Any) -> Any:
        return pydantic_encoder(object_)


class QuartSchema:
    """A Quart-Schema instance.

    This can be used to initialise Quart-Schema documentation a given
    app, either directly,

    .. code-block:: python

        app = Quart(__name__)
        QuartSchema(app)

    or via the factory pattern,

    .. code-block:: python

        quart_schema = QuartSchema()

        def create_app():
            app = Quart(__name__)
            quart_schema.init_app(app)
            return app

    This can be customised using the following arguments,

    Arguments:
        openapi_path: The path used to serve the openapi json on, or None
            to disable documentation.
        redoc_ui_path: The path used to serve the documentation UI using
            redoc or None to disable redoc documentation.
        swagger_ui_path: The path used to serve the documentation UI using
            swagger or None to disable swagger documentation.
        title: The publishable title for the app.
        version: The publishable version for the app.

    """

    def __init__(
        self,
        app: Optional[Quart] = None,
        *,
        openapi_path: Optional[str] = "/openapi.json",
        redoc_ui_path: Optional[str] = "/redocs",
        swagger_ui_path: Optional[str] = "/docs",
        title: Optional[str] = None,
        version: str = "0.1.0",
    ) -> None:
        self.openapi_path = openapi_path
        self.redoc_ui_path = redoc_ui_path
        self.swagger_ui_path = swagger_ui_path
        self.title = title
        self.version = version
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Quart) -> None:
        self.app = app
        self.title = self.app.name if self.title is None else self.title
        app.websocket_class = new_class("Websocket", (Websocket, WebsocketMixin))  # type: ignore
        app.json_encoder = JSONEncoder
        if self.openapi_path is not None:
            self.app.add_url_rule(self.openapi_path, "openapi", self.openapi)
            if self.redoc_ui_path is not None:
                self.app.add_url_rule(self.redoc_ui_path, "redoc_ui", self.redoc_ui)
            if self.swagger_ui_path is not None:
                self.app.add_url_rule(self.swagger_ui_path, "swagger_ui", self.swagger_ui)

    async def openapi(self) -> dict:
        paths: Dict[str, dict] = {}
        components = {"schemas": {}}  # type: ignore
        for rule in self.app.url_map.iter_rules():
            func = self.app.view_functions[rule.endpoint]
            response_schemas = getattr(func, QUART_SCHEMA_RESPONSE_ATTRIBUTE, {})
            request_schema = getattr(func, QUART_SCHEMA_REQUEST_ATTRIBUTE, None)
            querystring_schema = getattr(func, QUART_SCHEMA_QUERYSTRING_ATTRIBUTE, None)
            if response_schemas == {} and request_schema is None and querystring_schema is None:
                continue

            path_object = {  # type: ignore
                "parameters": [],
                "responses": {},
            }
            if func.__doc__ is not None:
                summary, *description = func.__doc__.splitlines()
                path_object["description"] = "\n".join(description)
                path_object["summary"] = summary

            for status_code, schema in response_schemas.items():
                definitions, schema = _split_definitions(schema)
                components["schemas"].update(definitions)
                path_object["responses"][status_code] = {  # type: ignore
                    "content": {
                        "application/json": {
                            "schema": schema,
                        },
                    },
                }

            if request_schema is not None:
                definitions, schema = _split_definitions(request_schema)
                components["schemas"].update(definitions)
                path_object["requestBody"] = {
                    "content": {
                        "application/json": {
                            "schema": schema,
                        },
                    },
                }

            if querystring_schema is not None:
                definitions, schema = _split_definitions(querystring_schema)
                components["schemas"].update(definitions)
                for name, type_ in schema["properties"].items():
                    path_object["parameters"].append(  # type: ignore
                        {
                            "name": name,
                            "in": "query",
                            "schema": type_,
                        }
                    )

            for name, converter in rule._converters.items():
                path_object["parameters"].append(  # type: ignore
                    {
                        "name": name,
                        "in": "path",
                    }
                )

            path = re.sub(PATH_RE, r"{\1}", rule.rule)
            paths.setdefault(path, {})

            for method in rule.methods:
                if method == "HEAD" or (method == "OPTIONS" and rule.provide_automatic_options):
                    continue
                paths[path][method.lower()] = path_object

        return {
            "openapi": "3.0.3",
            "info": {
                "title": self.title,
                "version": self.version,
            },
            "components": components,
            "paths": paths,
        }

    async def swagger_ui(self) -> str:
        return await render_template_string(
            SWAGGER_TEMPLATE,
            title=self.title,
            openapi_path=self.openapi_path,
            swagger_js_url="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/3.37.2/swagger-ui-bundle.js",  # noqa: E501
            swagger_css_url="https://cdnjs.cloudflare.com/ajax/libs/swagger-ui/3.37.2/swagger-ui.min.css",  # noqa: E501
        )

    async def redoc_ui(self) -> str:
        return await render_template_string(
            REDOC_TEMPLATE,
            title=self.title,
            openapi_path=self.openapi_path,
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js",
        )


def _split_definitions(schema: dict) -> Tuple[dict, dict]:
    new_schema = schema.copy()
    definitions = new_schema.pop("definitions", {})
    return definitions, new_schema
