# IBM Confidential
# OCO Source Materials
# 5737-B37, 5737-C49, 5737-H76
# (C) Copyright IBM Corp. 2021, 2022  All Rights Reserved.
# The source code for this program is not published or
# otherwise divested of its trade secrets, irrespective of
# what has been deposited with the U.S. Copyright Office.

import inspect
from typing import Any, Union, Iterable, Dict, TypeVar, Type, Optional, \
    overload

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal

from .client_errors import MissingValueError, UnexpectedTypeError, \
    ConfiguredTypeError, ConfiguredMissingError, StoragePropertiesNoFieldError, \
    StoragePropertiesFieldWrongTypeError, JsonFormatNoFieldError, \
    JsonFormatFieldWrongTypeError, ScopeResponseNoFieldError, \
    ScopeResponseFieldWrongTypeError, CredentialsNoFieldError, \
    CredentialsFieldWrongTypeError
from .cpd_paths import CpdScope

def validate_type(
        el: Any,
        el_name: str,
        expected_type: Union[type, Iterable[type]],
        mandatory: bool = True
):
    if mandatory and (el is None or el is inspect.Parameter.empty):
        raise MissingValueError(el_name)
    elif el is None:
        return

    if isinstance(expected_type, type):
        if not isinstance(el, expected_type):
            raise UnexpectedTypeError(el_name, expected_type, type(el))
    else: # Iterable
        try:
            next((x for x in expected_type if isinstance(el, x)))
            return True
        except StopIteration:
            return False

def validate_type_from_config(
        service_name: str,
        el: Any,
        el_name: str,
        expected_type: Union[type, Iterable[type]],
        mandatory: bool = True
):
    if mandatory and (el is None or el is inspect.Parameter.empty):
        raise ConfiguredMissingError(service_name, el_name)
    elif el is None:
        return

    if isinstance(expected_type, type):
        if not isinstance(el, expected_type):
            raise ConfiguredTypeError(service_name, el_name, expected_type, type(el))
    else: # Iterable
        try:
            next((x for x in expected_type if isinstance(el, x)))
            return True
        except StopIteration:
            return False

def validate_json_format_field(
    field_not_found: Type[JsonFormatNoFieldError],
    field_wrong_type: Type[JsonFormatFieldWrongTypeError],
):
    def inner(
        config: dict,
        el: Any,
        field_path: str,
        expected_type: Union[type, Iterable[type]],
        mandatory: bool = True
    ):
        if mandatory and (el is None or el is inspect.Parameter.empty):
            raise field_not_found(config, field_path)
        elif el is None:
            return

        if isinstance(expected_type, type):
            if not isinstance(el, expected_type):
                raise field_wrong_type(config, field_path, expected_type, type(el))
        else:  # Iterable
            try:
                next((x for x in expected_type if isinstance(el, x)))
                return True
            except StopIteration:
                return False
    return inner

validate_storage_config_field = validate_json_format_field(
    StoragePropertiesNoFieldError,
    StoragePropertiesFieldWrongTypeError,
)

validate_scope_response_field = validate_json_format_field(
    ScopeResponseNoFieldError,
    ScopeResponseFieldWrongTypeError,
)

validate_credentials_field = validate_json_format_field(
    CredentialsNoFieldError,
    CredentialsFieldWrongTypeError,
)


# cannot auto-generate generic methods :/


_ET = TypeVar('_ET')

@overload
def get_storage_config_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET]
) -> _ET: ...
@overload
def get_storage_config_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: Literal[True]
) -> _ET: ...
@overload
def get_storage_config_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: bool = True
) -> Optional[_ET]: ...

def get_storage_config_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: bool = True
) -> Optional[_ET]:
    parts = field_path.split('.')
    acc = []
    inner = config
    for i, part in enumerate(parts):
        last_one = i == len(parts)-1
        part_type = expected_type if last_one else dict

        acc.append(part)
        inner = inner.get(part, None)
        validate_storage_config_field(config, inner, ".".join(acc), part_type, mandatory=mandatory)
        if inner is None:
            break
    return inner


@overload
def get_scope_response_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET]
) -> _ET: ...
@overload
def get_scope_response_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: Literal[True]
) -> _ET: ...
@overload
def get_scope_response_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: bool = True
) -> Optional[_ET]: ...

def get_scope_response_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: bool = True
) -> Optional[_ET]:
    parts = field_path.split('.')
    acc = []
    inner = config
    for i, part in enumerate(parts):
        last_one = i == len(parts)-1
        part_type = expected_type if last_one else dict

        acc.append(part)
        inner = inner.get(part, None)
        validate_scope_response_field(config, inner, ".".join(acc), part_type, mandatory=mandatory)
        if inner is None:
            break
    return inner


@overload
def get_credentials_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET]
) -> _ET: ...
@overload
def get_credentials_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: Literal[True]
) -> _ET: ...
@overload
def get_credentials_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: bool = True
) -> Optional[_ET]: ...

def get_credentials_field(
        config: Dict[str, Any],
        field_path: str,
        expected_type: Type[_ET],
        mandatory: bool = True
) -> Optional[_ET]:
    parts = field_path.split('.')
    acc = []
    inner = config
    for i, part in enumerate(parts):
        last_one = i == len(parts)-1
        part_type = expected_type if last_one else dict

        acc.append(part)
        inner = inner.get(part, None)
        validate_credentials_field(config, inner, ".".join(acc), part_type, mandatory=mandatory)
        if inner is None:
            break
    return inner


def get_query_params_from_scope(
        scope: CpdScope,
) -> Dict[str, str]:
    scope_key = scope.scope_type()
    if scope_key.endswith('s'):
        scope_key = scope_key[:-1]
    scope_key += '_id'

    return {
        scope_key: scope.scope_id(),
    }
