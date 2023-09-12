# IBM Confidential
# OCO Source Materials
# 5737-B37, 5737-C49, 5737-H76
# (C) Copyright IBM Corp. 2021, 2022  All Rights Reserved.
# The source code for this program is not published or
# otherwise divested of its trade secrets, irrespective of
# what has been deposited with the U.S. Copyright Office.

import base64
import io
import json
import os
import warnings
from abc import abstractmethod, ABC
from collections import abc
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import cast, ClassVar, Mapping, Union, Optional, Any, Callable, \
    Tuple, TypedDict, Dict
from typing_extensions import NotRequired
from urllib.parse import urljoin, quote

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol

import requests
import attr
import inspect
import textwrap
from attr import attrs, fields_dict, NOTHING
from ibm_cloud_sdk_core import BaseService, DetailedResponse, ApiException
from ibm_cloud_sdk_core.authenticators import Authenticator, IAMAuthenticator, \
    CloudPakForDataAuthenticator, BearerTokenAuthenticator
import ibm_boto3
from ibm_botocore.client import Config
from kfp import components
from kfp.dsl import serialization_utils

from .client_errors import JsonParsingError, MissingValueError, \
    OfCpdPathError, NoWmlInstanceError, WmlServiceNameUnknownTypeError, \
    WmlServiceNameNoPrefixError, WmlServiceCNameNotValidError, \
    PublicCloudOnlyError, FilesResultsNotSupportedError, NoSuchOverloadError, \
    WmlUnknownAuthMethodError, ComponentNotFound
from .cpd_paths import CpdScope
from .utils import validate_type, \
    get_storage_config_field, get_scope_response_field, get_credentials_field, \
    get_query_params_from_scope


DEFAULT_PYTHON_BASE_IMAGE = 'cp.icr.io/cp/cpd/python-ubi-base-image:latest'


def public_cloud_only(fn: Callable) -> Callable:
    @wraps(fn)
    def inner(self: 'WatsonPipelines', *args, **kwargs):
        if not self.is_public:
            raise PublicCloudOnlyError("Function", fn.__name__)
        return fn(self, *args, **kwargs)

    return inner


class AuthMethod(Enum):
    APIKEY = 'apikey'
    BEARER_TOKEN = 'bearer_token'
    ANY = 'any'


@attrs(auto_attribs=True, frozen=True)
class ComponentMetadata:
    id: str

    name: str
    description: Optional[str] = None
    environment_id: Optional[str] = None

    created_at: Optional[str] = None
    annotations: Optional[dict[str, str]] = None
    labels: Optional[dict[str, str]] = None
    custom: Optional[dict[str, str]] = None
    latest_version: Optional[dict] = None

    def to_dict(self) -> dict:
        return attr.asdict(self)


class WatsonPipelines(BaseService):
    """Watson Pipelines client

    Communicates with Watson Pipelines to provide some high-level utilities.

    Arguments:
        apikey (str): API key the authenticator should be constructed from

    Keyword Arguments:
        service_name (str): name of the service used
        url (str): url of the service the client should communicate with
    """

    DEFAULT_SERVICE_URL = "https://api.dataplatform.cloud.ibm.com"
    DEFAULT_SERVICE_NAME = 'watson-pipelines'

    DEFAULT_CPD_API_URL = "https://api.dataplatform.cloud.ibm.com"

    SDK_NAME = 'ibm-watson-pipelines'

    @classmethod
    def new_instance(cls, *, service_name: str = None, url: str = None) -> 'WatsonPipelines':
        """
        Return a new Watson Pipelines client for default settings.
        """
        return cls(service_name=service_name, url=url)

    @classmethod
    def from_apikey(cls, apikey: str = None, *, service_name: str = None, url: str = None) -> 'WatsonPipelines':
        """
        Return a new Watson Pipelines client for the specified API key.
        """
        return cls(
            apikey=apikey,
            auth_method=AuthMethod.APIKEY,
            service_name=service_name,
            url=url,
        )

    @classmethod
    def from_token(cls, bearer_token: str = None, *, service_name: str = None, url: str = None) -> 'WatsonPipelines':
        """
        Return a new Watson Pipelines client for the specified bearer token.
        """
        return cls(
            bearer_token=bearer_token,
            auth_method=AuthMethod.BEARER_TOKEN,
            service_name=service_name,
            url=url,
        )

    def __init__(
        self,
        apikey: str = None,
        *,
        bearer_token: str = None,
        service_name: str = None,
        url: str = None,
        auth_method: AuthMethod = None,
    ):

        url = self._get_cpd_api_url(url)
        validate_type(url, "url", str)

        authenticator, is_public = self._get_authenticator(
            auth_method,
            apikey=apikey,
            bearer_token=bearer_token,
            url=url,
        )

        if service_name is None:
            service_name = self.DEFAULT_SERVICE_NAME

        super().__init__(
            service_url=url,
            authenticator=authenticator,
            disable_ssl_verification=not is_public,
        )
        self.authenticator = authenticator
        self.configure_service(service_name)
        self.is_public = is_public

    def _get_authenticator(
            self,
            auth_method: Optional[AuthMethod],
            apikey: Optional[str],
            bearer_token: Optional[str],
            url: str,
    ) -> Tuple[Authenticator, bool]:
        def censor_value(value: Optional[Any]) -> Optional[str]:
            if value is None:
                return None
            return '...'

        def no_such_overload() -> NoSuchOverloadError:
            class_name = type(self).__name__
            kwargs = {
                'apikey': censor_value(apikey),
                'bearer_token': censor_value(bearer_token),
                'auth_method': auth_method,
            }
            kwargs = {k: v for k, v in kwargs.items() if v is not None}
            return NoSuchOverloadError(class_name, [], kwargs)

        if apikey is not None and bearer_token is not None:
            raise no_such_overload()

        if auth_method == AuthMethod.APIKEY:
            if bearer_token is not None:
                raise no_such_overload()
            if apikey is None:
                apikey = os.environ.get('APIKEY', None)
            if apikey is None:
                raise MissingValueError('APIKEY')
            return self._get_authenticator_from_api_key(apikey, url)

        elif auth_method == AuthMethod.BEARER_TOKEN:
            if apikey is not None:
                raise no_such_overload()
            if bearer_token is None:
                bearer_token = os.environ.get('USER_ACCESS_TOKEN')
            if bearer_token is None:
                raise MissingValueError('USER_ACCESS_TOKEN')
            return self._get_authenticator_from_bearer_token(bearer_token, url)

        elif auth_method == AuthMethod.ANY or auth_method is None:
            if apikey is not None:
                return self._get_authenticator_from_api_key(apikey, url)
            elif bearer_token is not None:
                return self._get_authenticator_from_bearer_token(bearer_token, url)

            apikey = os.environ.get('APIKEY', None)
            if apikey is not None:
                return self._get_authenticator_from_api_key(apikey, url)

            bearer_token = os.environ.get('USER_ACCESS_TOKEN')
            if bearer_token is not None:
                return self._get_authenticator_from_bearer_token(bearer_token, url)

            # should provide either APIKEY or USER_ACCESS_TOKEN, usually APIKEY
            raise MissingValueError('APIKEY')
        else:
            raise no_such_overload()

    def _get_iam_url(self, url: str) -> Tuple[str, bool]:
        validate_type(url, "url", str)
        if not url.startswith("https://"):
            warnings.warn("'url' doesn't start with https")

        url_to_iam_url = {
            "dev.cloud.ibm.com":  "https://iam.test.cloud.ibm.com/identity/token",
            "test.cloud.ibm.com": "https://iam.cloud.ibm.com/identity/token",
            "cloud.ibm.com":      "https://iam.cloud.ibm.com/identity/token",
        }
        is_public = True
        iam_url = None

        # ignore api. and optionally the region-specific prefix, start with `dataplatform`
        # for instance:
        #   https://api.dataplatform.cloud.ibm.com
        #   https://api.eu-de.dataplatform.cloud.ibm.com
        # both have root_url=cloud.ibm.com
        root_idx = url.find('dataplatform')
        if root_idx != -1:
            root_url = url[root_idx+len('dataplatform')+1 :]
            iam_url = url_to_iam_url.get(root_url)

        if iam_url is None:
            # assume it's CPD
            is_public = False
            iam_url = url + "/icp4d-api/v1/authorize"
        return iam_url, is_public

    def _get_authenticator_from_api_key(self, apikey: str, url: str) -> Tuple[Authenticator, bool]:
        validate_type(apikey, "api_key", str)
        iam_url, is_public = self._get_iam_url(url)
        if is_public:
            auth = IAMAuthenticator(apikey, url=iam_url)
        else:
            auth = CloudPakForDataAuthenticator("admin", url=iam_url, apikey=apikey, disable_ssl_verification=True)
        self.auth_method = AuthMethod.APIKEY
        # for backwards-compatibility, WSPipelines directly gets the `apikey` field
        self.apikey = apikey
        return auth, is_public

    def _get_authenticator_from_bearer_token(self, bearer_token: str, url: str) -> Tuple[Authenticator, bool]:
        validate_type(bearer_token, "bearer_token", str)
        iam_url, is_public = self._get_iam_url(url)
        auth = BearerTokenAuthenticator(bearer_token=bearer_token)
        self.auth_method = AuthMethod.BEARER_TOKEN
        # to stay consistent with `apikey`, WSPipelines directly gets the `bearer_token` field
        self.bearer_token = bearer_token
        return auth, is_public

    @classmethod
    def get_output_artifact_map(cls) -> Mapping[str, str]:
        """Get output artifacts key-value map from env-var OF_OUTPUT_ARTIFACTS."""
        output_artifacts = os.environ.get('OF_OUTPUT_ARTIFACTS', None)
        if output_artifacts is None:
            raise MissingValueError("OF_OUTPUT_ARTIFACTS")

        try:
            output_artifacts = json.loads(output_artifacts)
        except json.decoder.JSONDecodeError as ex:
            # could it be base64?
            try:
                pad_base64 = lambda s: s + '=' * (-len(s) % 4)
                output_artifacts = base64.b64decode(pad_base64(output_artifacts))
                output_artifacts = json.loads(output_artifacts)
            except:
                # if it has been decoded, show the decoded value
                raise JsonParsingError(output_artifacts, ex)

        validate_type(output_artifacts, "OF_OUTPUT_ARTIFACTS", abc.Mapping)
        for output_name, output_artifact in output_artifacts.items():
            validate_type(output_artifact, f"OF_OUTPUT_ARTIFACTS[{output_name}]", str)
        output_artifacts = cast(Mapping[str, str], output_artifacts)
        return output_artifacts

    def get_project(self, scope_id: str, *, context: Optional[str] = None) -> dict:
        """Get project of given ID."""
        uri = urljoin("/v2/projects/", scope_id)
        scope = self._get_scope_from_uri(uri, context=context)
        return scope

    def get_space(self, scope_id: str, *, context: Optional[str] = None) -> dict:
        """Get space of given ID."""
        uri = urljoin("/v2/spaces/", scope_id)
        scope = self._get_scope_from_uri(uri, context=context)
        return scope

    def _get_scope_from_uri(self, uri: str, *, context: Optional[str] = None):
        headers = {
            "Accept": "application/json",
        }
        params = {}
        if context is not None:
            params["context"] = context

        scope_request = self.prepare_request('GET', uri, headers=headers, params=params)
        # BaseService has some type signature problems here
        scope_request = cast(requests.Request, scope_request)

        scope_response = self.send(scope_request)

        if isinstance(scope_response.result, dict):
            scope = scope_response.result
        else:
            try:
                scope = json.loads(scope_response.result.content)
            except json.decoder.JSONDecodeError as ex:
                if hasattr(scope_response.result, 'content'):
                    raise JsonParsingError(scope_response.result.content, ex)
                else:
                    raise JsonParsingError(scope_response.result, ex)
        return scope

    def _get_cpd_api_url(self, url: str = None) -> str:
        if url is not None:
            return url

        name = 'OF_CPD_API_URL'
        url = os.environ.get('OF_CPD_API_URL', None)

        if url is None:
            name = 'RUNTIME_ENV_APSX_URL'
            url = os.environ.get('RUNTIME_ENV_APSX_URL', None)

        if url is None:
            name = 'DEFAULT_CPD_API_URL'
            url = self.DEFAULT_CPD_API_URL

        validate_type(url, name, str)
        return url

    def get_scope(
            self,
            cpd_scope: Optional[Union[str, CpdScope]] = None
    ) -> dict:
        """Get scope given its CPDPath."""
        cpd_scope = self.get_scope_cpdpath(cpd_scope)

        class ScopeGetter(Protocol):
            @abstractmethod
            def __call__(self, scope_id: str, *, context: Optional[str] = None) -> dict: ...

        scope_type_map: Mapping[str, ScopeGetter] = {
            'projects': self.get_project,
            'spaces': self.get_space,
        }

        scope_getter = scope_type_map.get(cpd_scope.scope_type(), None)
        if scope_getter is None:
            li = ', '.join(scope_type_map.keys())
            msg = "Handling scopes other than {} is not supported yet!".format(li)
            raise NotImplementedError(msg)

        ctx = cpd_scope.context()
        if ctx == '':
            ctx = None

        if cpd_scope.scope_id() is None:
            raise RuntimeError("CpdScope in get_scope cannot be query-type")

        scope = scope_getter(cpd_scope.scope_id(), context=ctx)
        return scope

    @classmethod
    def _extract_storage_properties(
            cls,
            scope_response: dict
    ) -> dict:
        props = get_scope_response_field(scope_response, 'entity.storage.properties', dict)
        return props

    @classmethod
    def _extract_storage_guid(
            cls,
            scope_response: dict
    ) -> str:
        guid = get_scope_response_field(scope_response, 'entity.storage.guid', str)
        return guid

    @public_cloud_only
    def get_wml_credentials(
            self,
            cpd_scope: Optional[Union[str, CpdScope]] = None
    ) -> dict:
        """Get WML credentials given scope's CPDPath.

        Note: this is a public-cloud-only feature. For CPD, only the address
        and API key are needed, no credentials."""
        # make sure cpd_scope is not-None, as _extract_wml_creds_from_scope_response
        # needs it that way
        cpd_scope = self.get_scope_cpdpath(cpd_scope)
        scope_response = self.get_scope(cpd_scope)

        wml_credentials = self._extract_wml_creds_from_scope_response(
            cpd_scope,
            scope_response
        )
        return wml_credentials.to_dict()

    def _extract_wml_creds_from_scope_response(
            self,
            cpd_scope: CpdScope,
            scope_response: dict
    ) -> 'WmlCredentials':
        computed = get_scope_response_field(
            scope_response, "entity.compute", list, mandatory=False
        )

        data = None
        for el in computed or []:
            if 'type' in el and el['type'] == 'machine_learning':
                data = el
                break

        if data is None:
            raise NoWmlInstanceError(cpd_scope)

        return self._extract_wml_creds_from_computed(cpd_scope, data)

    def _extract_wml_creds_from_computed(
            self,
            cpd_scope: CpdScope,
            computed: dict
    ) -> 'WmlCredentials':
        guid = get_credentials_field(computed, "guid", str)
        name = get_credentials_field(computed, "name", str)
        crn = get_credentials_field(computed, "crn", str)
        url = self._get_wml_url_from_wml_crn(crn)

        if hasattr(self, 'apikey'):
            auth = WmlCredentialsApiKey(self.apikey)
        elif hasattr(self, 'bearer_token'):
            auth = WmlCredentialsBearerToken(self.bearer_token)
        else:
            raise WmlUnknownAuthMethodError(cpd_scope, str(self.auth_method.value))

        return WmlCredentials(
            guid = guid,
            name = name,
            url = url,
            auth = auth,
        )

    def _get_wml_url_from_wml_crn(self, crn: str) -> str:
        wml_prod = 'https://{}.ml.cloud.ibm.com'
        wml_qa = 'https://yp-qa.ml.cloud.ibm.com'
        wml_staging = 'https://{}.ml.test.cloud.ibm.com'
        wml_service_name = 'pm-20'
        wml_service_name_devops = 'pm-20-devops'
        platform_qa_url_host = 'api.dataplatform.test.cloud.ibm.com'

        parts = crn.split(':')

        cname = parts[2]
        service_name = parts[4]
        location = parts[5]

        if not service_name.startswith(wml_service_name):
            raise WmlServiceNameNoPrefixError(crn, service_name, wml_service_name)

        if cname == 'bluemix':
            if platform_qa_url_host in self.service_url:
                return wml_qa
            else:
                return wml_prod.format(location)
        elif cname == 'staging':
            if service_name == wml_service_name:
                return wml_staging.format('us-south')
            elif service_name == wml_service_name_devops:
                return wml_staging.format('wml-fvt')
            else:
                raise WmlServiceNameUnknownTypeError(crn, service_name)
        else:
            raise WmlServiceCNameNotValidError(crn, cname, ['bluemix', 'staging'])

    @public_cloud_only
    def get_storage_credentials(
        self,
        cpd_scope: Optional[Union[str, CpdScope]] = None
    ) -> dict:
        """Get storage credentials given scope's CPDPath.

        Note: this is a public-cloud-only feature. For CPD, only the address
        and API key are needed, no credentials."""
        scope_response = self.get_scope(cpd_scope)
        props = self._extract_storage_properties(scope_response)

        cos_credentials = StorageCredentialsFull.from_storage_properties(props)
        return cos_credentials.to_dict()

    @classmethod
    def get_scope_cpdpath(
        cls,
        cpd_scope: Optional[Union[str, CpdScope]] = None
    ) -> CpdScope:
        """Get the scope as CpdScope.

         The operation performed depends on the data type passed:
         * given ``None``, the default scope will be retrieved from environmental variable
         * given a string, it will be parsed to a ``CpdScope``
         * given a ``CpdScope``, it will be returned as-is (i.e. it's a no-op)

         Mostly useful with zero arguments (to retrieve the default scope)
         or when handling ``Union[str, CpdScope]``."""
        name = "cpd_scope"

        # if cpd_scope is None --- get it from env-var
        def get_scope_from_env_var() -> Tuple[Optional[str], str]:
            result = os.environ.get('OF_CPD_SCOPE', None)
            if result is not None:
                return result, 'OF_CPD_SCOPE'

            result = os.environ.get('PROJECT_ID', None)
            if result is not None:
                return 'cpd:///projects/' + result, 'PROJECT_ID'

            result = os.environ.get('SPACE_ID', None)
            if result is not None:
                return 'cpd:///spaces/' + result, 'SPACE_ID'

            # default env-var
            return None, 'OF_CPD_SCOPE'

        if cpd_scope is None:
            cpd_scope, name = get_scope_from_env_var()
            if cpd_scope is None:
                raise MissingValueError(name)

        # if cpd_scope is str --- parse it
        if isinstance(cpd_scope, str):
            try:
                cpd_scope = CpdScope.from_string(cpd_scope)
            except Exception as ex:
                raise OfCpdPathError(cpd_scope, reason = ex)

        # now it should be CpdScope
        validate_type(cpd_scope, name, CpdScope)
        return cpd_scope

    def _default_path_to_result(self, result_name: str) -> str:
        return f'.ibm_watson_pipelines/results/{result_name}'

    def _store_results_via_client(
        self,
        storage_client: 'StorageClient',
        outputs: Mapping[str, Any], # output name -> value
        output_artifacts: Optional[Mapping[str, str]] = None,
    ) -> DetailedResponse:
        if output_artifacts is None:
            output_artifacts = {
                out: self._default_path_to_result(out) for out in outputs.keys()
            }

        response = None
        for output_name, output_value in outputs.items():
            if output_name not in output_artifacts:
                print(
                    f'Variable {output_name} is not on the list of output variables defined by pipeline component, '
                    f'check your pipeline definition for possible typos and omissions')
                continue

            result_key = output_artifacts[output_name]
            response = storage_client.store_result(output_name, result_key, output_value)
        return response

    def store_results(
        self,
        outputs: Mapping[str, Any], # output name -> value
    ) -> DetailedResponse:
        """Store notebook's results."""
        validate_type(outputs, "outputs", abc.Mapping)
        for key, value in outputs.items():
            validate_type(key, f"outputs[...]", str)

        test_mode = False
        try:
            output_artifacts = self.get_output_artifact_map()
        except MissingValueError:
            test_mode = True
            output_artifacts = {
                out: self._default_path_to_result(out) for out in outputs.keys()
            }

        if test_mode:
            print("Running outside of Watson Pipelines - storing results in the local filesystem for testing purposes...")

        cpd_scope = self.get_scope_cpdpath()  # needed for CPD variant anyway
        scope = self.get_scope(cpd_scope)

        storage_client: StorageClient

        if self.is_public:
            props = self._extract_storage_properties(scope)
            cos_config = StorageConfig.from_storage_properties(props)
            storage_client = CosClient(self, cos_config)
        else:
            guid = self._extract_storage_guid(scope)
            storage_client = CamsClient(self, cpd_scope, guid)

        if test_mode:
            print("")
            print("  output paths:")
            for out_name, out_path in output_artifacts.items():
                print(f'    - "{out_name}": {out_path}')
            storage_client = LocalFileSystemClient(self)

        return self._store_results_via_client(storage_client, outputs, output_artifacts)

    def _get_cpd_scope_from_args(
            self,
            cpd_scope: Optional[Union[str, CpdScope]] = None,
            project_id: Optional[str] = None
    ) -> CpdScope:

        if project_id is not None:
            cpd_scope = CpdScope.from_string("/projects/{}".format(project_id))

        return self.get_scope_cpdpath(cpd_scope)

    def publish_component(
            self,
            name: str,
            func: Callable,
            description: Optional[str] = None,
            environment_id: Optional[str] = None,
            base_image: Optional[str] = None,
            packages_to_install: Optional[list[str]] = None,
            cpd_scope: Optional[Union[str, CpdScope]] = None,
            project_id: Optional[str] = None,
            overwrite: Optional[bool] = None
    ) -> ComponentMetadata:
        """Publish function as a new component"""
        cpd_scope = self._get_cpd_scope_from_args(cpd_scope, project_id)

        annotations: Dict[str, str] = {
            'component_group': 'user',
            'component_type': getattr(func, '__name__', 'user_function'),
            'component_type_name': name,
            'component_version': '1'
        }

        if base_image is None:
            base_image = DEFAULT_PYTHON_BASE_IMAGE

        factory = components.create_component_from_func(
            func=func,
            annotations=annotations,
            base_image=base_image,
            packages_to_install=packages_to_install,
        )

        template = factory.component_spec.to_dict()
        template['name'] = name
        if description is not None:
            template['description'] = description

        yaml = serialization_utils.yaml_dump(template)

        func_code = inspect.getsource(func)
        func_code = textwrap.dedent(func_code)

        component = NewComponentData(
            name=name,
            description=description,
            environment_id=environment_id,
            template=yaml,
            source=ComponentSource(
                type="python_function",
                content=func_code,
            )
        )

        components_client = ComponentsClient(self, cpd_scope)
        try:
            return components_client.publish_component(component)
        except ApiException as e:
            if overwrite and e.code == 409:
                # 409 - Conflict - already exists
                return components_client.update_component(component)
            else:
                raise

    def get_components(
            self,
            cpd_scope: Optional[Union[str, CpdScope]] = None,
            project_id: Optional[str] = None
    ) -> list[ComponentMetadata]:
        """List all components from the provided scope (project or space)"""
        cpd_scope = self._get_cpd_scope_from_args(cpd_scope, project_id)
        components_client = ComponentsClient(self, cpd_scope)
        return components_client.get_components()

    def get_component(
            self,
            component_id: Optional[str] = None,
            name: Optional[str] = None,
            cpd_scope: Optional[Union[str, CpdScope]] = None,
            project_id: Optional[str] = None
    ) -> ComponentMetadata:
        """Get component by ID or name from the provided scope (project or space)"""
        if component_id is None and name is None:
            raise MissingValueError(['component_id', 'name'])
        cpd_scope = self._get_cpd_scope_from_args(cpd_scope, project_id)
        components_client = ComponentsClient(self, cpd_scope)
        if component_id is not None:
            return components_client.get_component(component_id)
        else:
            component = components_client.get_component_by_name(name)
            if component is None:
                raise ComponentNotFound(name)
            return component

    def delete_component(
            self,
            component_id: str,
            cpd_scope: Optional[Union[str, CpdScope]] = None,
            project_id: Optional[str] = None
    ):
        """Delete the existing component"""
        cpd_scope = self._get_cpd_scope_from_args(cpd_scope, project_id)
        components_client = ComponentsClient(self, cpd_scope)
        return components_client.delete_component(component_id)


@attrs(auto_attribs=True, frozen=True)
class WmlCredentialsAuth:
    def to_dict(self) -> dict:
        return attr.asdict(self)


@attrs(auto_attribs=True, frozen=True)
class WmlCredentialsApiKey(WmlCredentialsAuth):
    apikey: str


@attrs(auto_attribs=True, frozen=True)
class WmlCredentialsBearerToken(WmlCredentialsAuth):
    bearer_token: str


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class WmlCredentials:
    guid: str
    name: str
    url: str
    auth: WmlCredentialsAuth

    def to_dict(self) -> dict:
        result = attr.asdict(self)

        # inline `auth` field
        auth = result['auth']
        del result['auth']
        result.update(auth)

        return result


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class StorageCredentials:
    api_key: str
    service_id: str
    access_key_id: Optional[str] = None
    secret_access_key: Optional[str] = None
    resource_key_crn: Optional[str] = None

    def to_dict(self) -> dict:
        return attr.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'StorageCredentials':
        fields = dict()
        for field_name, field in fields_dict(cls).items():
            field_required = field.default is NOTHING
            value = get_credentials_field(data, field_name, str, mandatory=field_required)
            if value is not None:
                fields[field_name] = value

        return cls(**fields)


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class StorageCredentialsFull:
    admin: Optional[StorageCredentials] = None
    editor: Optional[StorageCredentials] = None
    viewer: StorageCredentials

    def to_dict(self) -> dict:
        return attr.asdict(self)

    @classmethod
    def from_storage_properties(cls, props: dict) -> 'StorageCredentialsFull':
        fields = dict()
        for field_name, field in fields_dict(cls).items():
            field_required = field.default is NOTHING
            path = "credentials." + field_name
            value = get_storage_config_field(props, path, dict, mandatory=field_required)
            if value is not None:
                fields[field_name] = StorageCredentials.from_dict(value)

        return cls(**fields)


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class StorageConfig:
    DEFAULT_COS_AUTH_ENDPOINT: ClassVar[str] = 'https://iam.cloud.ibm.com/identity/token'

    endpoint: str
    api_key_id: str
    instance_crn: str
    auth_endpoint: str
    bucket_name: str

    @classmethod
    def from_storage_properties(cls, props: dict) -> 'StorageConfig':
        fields_to_paths = {
            "endpoint": "endpoint_url",
            "bucket_name": "bucket_name",
            "api_key_id": "credentials.editor.api_key",
            "instance_crn": "credentials.editor.resource_key_crn",
        }
        fields = dict()
        for field_name, field_path in fields_to_paths.items():
            fields[field_name] = get_storage_config_field(props, field_path, str)
        fields["auth_endpoint"] = cls._get_auth_endpoint_from_instance_crn(fields["instance_crn"])

        return cls(**fields)

    @classmethod
    def _get_auth_endpoint_from_instance_crn(cls, instance_crn: str) -> str:
        parts = instance_crn.split(":")
        cname = parts[2]
        cname_to_auth_endpoint = {
            'bluemix': 'https://iam.cloud.ibm.com/identity/token',
            'prod': 'https://iam.cloud.ibm.com/identity/token',
            'staging': 'https://iam.test.cloud.ibm.com/identity/token',
            'dev': 'https://iam.test.cloud.ibm.com/identity/token',
        }
        auth_endpoint = cname_to_auth_endpoint.get(cname, cls.DEFAULT_COS_AUTH_ENDPOINT)
        validate_type(auth_endpoint, "auth_endpoint", str)
        return auth_endpoint


class StorageClient(ABC):
    def store_result(self, output_name: str, output_key: str, value: Any) -> DetailedResponse:
        validate_type(output_name, "output_name", str)
        validate_type(output_key, "output_key", str)

        if isinstance(value, io.TextIOBase):
            # not supported yet
            raise FilesResultsNotSupportedError(output_name)
        elif isinstance(value, str):
            str_value = value
        else:
            str_value = json.dumps(value)

        return self._store_str_result(output_name, output_key, str_value)

    @abstractmethod
    def _store_str_result(self, output_name: str, output_key: str, value: str) -> DetailedResponse: ...

class LocalFileSystemClient(StorageClient):
    def __init__(
        self,
        cpd_orchestration: WatsonPipelines
    ):
        validate_type(cpd_orchestration, "cpd_orchestration", WatsonPipelines)
        self.cpd_orchestration = cpd_orchestration

    def _store_str_result(self, output_name: str, output_key: str, value: str) -> DetailedResponse:
        path = Path(output_key)

        status = 201  # created a new file
        if path.exists():
            status = 202  # updated an existing file

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

        response = requests.Response()
        response.request = requests.Request(
            method = 'PUT',
            url = path.resolve().as_uri(),
            headers = {},
        )
        response.status_code = status
        response.headers = {}
        return DetailedResponse(
            response=response
        )

class CosClient(StorageClient):
    def __init__(
        self,
        cpd_orchestration: WatsonPipelines,
        config: StorageConfig
    ):
        validate_type(cpd_orchestration, "cpd_orchestration", WatsonPipelines)
        validate_type(config, "config", StorageConfig)

        self.cpd_orchestration = cpd_orchestration
        self.config = config
        self.cos = ibm_boto3.resource(
            "s3",
            ibm_api_key_id=config.api_key_id,
            ibm_service_instance_id=config.instance_crn,
            ibm_auth_endpoint=config.auth_endpoint,
            config=Config(signature_version="oauth"),
            endpoint_url=config.endpoint
        )

    def _store_str_result(self, output_name: str, output_key: str, value: str) -> DetailedResponse:
        cos_response = self.cos.Object(self.config.bucket_name, output_key).put(
            Body=value
        )
        response = requests.Response()
        response.request = requests.Request(
            method = 'PUT',
            url = urljoin(urljoin(self.config.endpoint, self.config.bucket_name), output_key),
            headers = {},
        )
        response.status_code = cos_response['ResponseMetadata']['HTTPStatusCode']
        response.headers = cos_response['ResponseMetadata']['HTTPHeaders']
        return DetailedResponse(
            response=response
        )


class CamsClient(StorageClient):
    def __init__(
        self,
        cpd_orchestration: WatsonPipelines,
        scope: CpdScope,
        guid: str,
    ):
        validate_type(cpd_orchestration, "cpd_orchestration", WatsonPipelines)
        validate_type(scope, "scope", CpdScope)
        validate_type(guid, "guid", str)

        self.cpd_orchestration = cpd_orchestration
        self.scope = scope
        self.guid = guid

    def _store_str_result(self, output_name: str, output_key: str, value: str) -> DetailedResponse:
        headers = {
            "Accept": "application/json",
        }

        params = get_query_params_from_scope(self.scope)

        if self.scope.context is not None and self.scope.context != '':
            params["context"] = self.scope.context

        asset_uri_prefix = '/v2/asset_files/'
        asset_file_uri = quote(output_key, safe='')
        uri = urljoin(asset_uri_prefix, asset_file_uri)

        files = {
            "file": (output_key.split('/')[-1], value, "application/octet-stream")
        }

        req = self.cpd_orchestration.prepare_request('PUT', uri, headers=headers, params=params, files=files)
        req = cast(requests.Request, req)
        res = self.cpd_orchestration.send(req)
        return res


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class ComponentSource:
    type: str
    content: str

    def to_dict(self) -> dict:
        return attr.asdict(self)


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class ComponentPatchOp:
    op: str
    path: str
    value: Union[str, dict]

    def to_dict(self) -> dict:
        return attr.asdict(self)


@attrs(auto_attribs=True, kw_only=True, frozen=True)
class NewComponentData:
    name: str
    template: str
    source: ComponentSource
    description: Optional[str] = None
    environment_id: Optional[str] = None

    def to_dict(self) -> dict:
        return attr.asdict(self)

    def _get_patch_ops(self, existing_component: ComponentMetadata) -> list[ComponentPatchOp]:
        ops: list[ComponentPatchOp] = []
        for prop_name in ['name', 'description', 'environment_id']:
            val = self.__getattribute__(prop_name)
            existing_val = existing_component.__getattribute__(prop_name)
            if val != existing_val:
                ops.append(ComponentPatchOp(op="replace", path="/" + prop_name, value=val))

        ops.append(ComponentPatchOp(op="replace", path="/template", value=self.template))
        ops.append(ComponentPatchOp(op="replace", path="/source", value=self.source.to_dict()))

        return ops


class ComponentsClient:
    def __init__(
            self,
            cpd_orchestration: WatsonPipelines,
            scope: CpdScope,
    ):
        validate_type(cpd_orchestration, "cpd_orchestration", WatsonPipelines)
        validate_type(scope, "scope", CpdScope)

        self.cpd_orchestration = cpd_orchestration
        self.scope = scope

    def publish_component(self, component: NewComponentData) -> ComponentMetadata:
        headers = {
            "Accept": "application/json",
        }

        params = get_query_params_from_scope(self.scope)

        uri = '/apis/v1beta1/components'
        data = component.to_dict()

        req = self.cpd_orchestration.prepare_request('POST', uri, headers=headers, params=params, data=data)
        req = cast(requests.Request, req)
        res = self.cpd_orchestration.send(req)

        return ComponentMetadata(**res.result)

    def update_component(self, component: NewComponentData) -> ComponentMetadata:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json-patch+json",
        }

        existing_component = self.get_component_by_name(component.name)
        if existing_component is None:
            raise ComponentNotFound(component.name)
        patch_ops = component._get_patch_ops(existing_component)
        patch_body = json.dumps([op.to_dict() for op in patch_ops])

        if len(patch_body) == 0:
            return existing_component

        params = get_query_params_from_scope(self.scope)

        uri = '/apis/v1beta1/components/{}'.format(existing_component.id)

        req = self.cpd_orchestration.prepare_request('PATCH', uri, headers=headers, params=params, data=patch_body)
        req = cast(requests.Request, req)
        res = self.cpd_orchestration.send(req)

        return ComponentMetadata(**res.result)

    def get_components(self, params: dict[str, str] = None) -> list[ComponentMetadata]:
        headers = {
            "Accept": "application/json",
        }

        scope_params = get_query_params_from_scope(self.scope)
        params = {} if params is None else params
        params = {**params, **scope_params}

        uri = '/apis/v1beta1/components'

        req = self.cpd_orchestration.prepare_request('GET', uri, headers=headers, params=params)
        req = cast(requests.Request, req)
        res = self.cpd_orchestration.send(req)

        collection = []
        if 'components' in res.result:
            collection = [ComponentMetadata(**c) for c in res.result['components']]
        return collection

    def get_component(self, component_id: str) -> ComponentMetadata:
        headers = {
            "Accept": "application/json",
        }

        params = get_query_params_from_scope(self.scope)

        uri = '/apis/v1beta1/components/{}'.format(component_id)

        req = self.cpd_orchestration.prepare_request('GET', uri, headers=headers, params=params)
        req = cast(requests.Request, req)
        res = self.cpd_orchestration.send(req)
        return ComponentMetadata(**res.result)

    def get_component_by_name(self, name: str) -> Optional[ComponentMetadata]:
        result = self.get_components({'name': name})
        return next((c for c in result if c.name == name), None)

    def delete_component(self, component_id: str):
        headers = {
            "Accept": "application/json",
        }

        params = get_query_params_from_scope(self.scope)

        uri = '/apis/v1beta1/components/{}'.format(component_id)

        req = self.cpd_orchestration.prepare_request('DELETE', uri, headers=headers, params=params)
        req = cast(requests.Request, req)
        self.cpd_orchestration.send(req)
