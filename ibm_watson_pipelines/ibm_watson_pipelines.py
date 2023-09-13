import os

from collections import abc
from enum import Enum
from typing import Tuple, Optional, Any, Mapping

from ibm_cloud_sdk_core import BaseService, DetailedResponse, ApiException
from ibm_cloud_sdk_core.authenticators import  Authenticator,BearerTokenAuthenticator
from .utils import validate_type
from .client_errors import MissingValueError

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