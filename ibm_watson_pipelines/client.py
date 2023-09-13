import os,io,json,base64,requests,traceback

from pprint import pprint
from collections import abc
from abc import ABC, abstractmethod
from enum import Enum
from typing import Tuple, Optional, Any, Mapping, Union, cast, Protocol

from ibm_cloud_sdk_core import BaseService, DetailedResponse, ApiException
from ibm_cloud_sdk_core.authenticators import  Authenticator,BearerTokenAuthenticator
from .utils import validate_type, get_scope_response_field, get_query_params_from_scope
from .client_errors import MissingValueError, FilesResultsNotSupportedError, JsonParsingError
from .cpd_paths import CpdScope
from urllib.parse import urljoin, quote

class AuthMethod(Enum):
    APIKEY = 'apikey'
    BEARER_TOKEN = 'bearer_token'
    ANY = 'any'

class WatsonPipelines(BaseService):
    DEFAULT_SERVICE_NAME = 'watson-pipelines'
    DEFAULT_CPD_API_URL = "https://api.dataplatform.cloud.ibm.com"
    SDK_NAME = 'ibm-watson-pipelines'

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

    def _get_cpd_api_url(self, url: str = None) -> str:
        print(f"_get_cpd_api_url({url}) is called....")
        if url is not None:
            return url

        name = 'OF_CPD_API_URL'
        url = os.environ.get('OF_CPD_API_URL', None)
        if url is not None:
            print(f"URL is from os.environ['OF_CPD_API_URL']: {os.environ['OF_CPD_API_URL']}")

        if url is None:
            name = 'RUNTIME_ENV_APSX_URL'
            print(f"URL is from os.environ['RUNTIME_ENV_APSX_URL']: {os.environ['RUNTIME_ENV_APSX_URL']}")
            url = os.environ.get('RUNTIME_ENV_APSX_URL', None)

        if url is None:
            name = 'DEFAULT_CPD_API_URL'
            print(f"url is from DEFAULT_CPD_API_URL: {DEFAULT_CPD_API_URL}")
            url = self.DEFAULT_CPD_API_URL

        validate_type(url, name, str)
        return url
    
    def _get_authenticator(
            self,
            auth_method: Optional[AuthMethod],
            apikey: Optional[str],
            bearer_token: Optional[str],
            url: str,
    ) -> Tuple[Authenticator, bool]:
        validate_type(bearer_token, "bearer_token", str)
        iam_url = url + "/icp4d-api/v1/authorize"
        is_public = False
        print(f"_get_authenticator_from_bearer_token() is called..., iam_url: {iam_url}")
        auth = BearerTokenAuthenticator(bearer_token=bearer_token)
        self.auth_method = AuthMethod.BEARER_TOKEN
        # to stay consistent with `apikey`, WSPipelines directly gets the `bearer_token` field
        self.bearer_token = bearer_token
        return auth, is_public     

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
            raise Exception("Running in test mode in Jupyter Notebook is not supported")
            #output_artifacts = {
            #    out: self._default_path_to_result(out) for out in outputs.keys()
            #}

        if test_mode:
            print("Running outside of Watson Pipelines - storing results in the local filesystem for testing purposes...")

        cpd_scope = self.get_scope_cpdpath()  # needed for CPD variant anyway
        scope = self.get_scope(cpd_scope)

        storage_client: StorageClient

        if self.is_public:
            raise Exception("Unsupported public instance")
            #props = self._extract_storage_properties(scope)
            #cos_config = StorageConfig.from_storage_properties(props)
            #storage_client = CosClient(self, cos_config)
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

    @classmethod
    def _extract_storage_guid(
            cls,
            scope_response: dict
    ) -> str:
        guid = get_scope_response_field(scope_response, 'entity.storage.guid', str)
        return guid

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
            print(f"cpd_scope is None, assigned: {cpd_scope}")
            if cpd_scope is None:
                raise MissingValueError(name)

        # if cpd_scope is str --- parse it
        if isinstance(cpd_scope, str):
            try:
                cpd_scope = CpdScope.from_string(cpd_scope)
                print(f"cpd_scope is str... calling CpdScope.from_string()... {type(cpd_scope)}")
            except Exception as ex:
                raise OfCpdPathError(cpd_scope, reason = ex)

        # now it should be CpdScope
        validate_type(cpd_scope, name, CpdScope)

        return cpd_scope

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
    
    def send(self, request: requests.Request, **kwargs) -> DetailedResponse:
        print("INFO *************** In send()... *****************")
        pprint(request)
        #traceback.print_stack()
        print(''.join(traceback.format_stack()[-4:]))
        #pprint(vars(request))
        #pprint(dir(request))
        resp = super(WatsonPipelines,self).send(request,**kwargs)
        pprint(vars(resp))
        pprint(dir(resp))
        print("*************** In send()... *****************")
        return resp

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
        print(f"INFO: ********** in class LocalFileSystemClient() **************")
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