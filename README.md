# IBM Watson Pipelines Python Client

This is a scale down IBM Watson Pipelines Python client based on original code in PyPi package ibm_watson_pipelines

- This is tested in CPD 4.7.0 Watson Pipelines Jupyter notebook node
- It does not work in IBM cloud environment
- It does not run in non watson pipeline node
- It only support store_results method

## Sample Code

```py
!pip install https://github.com/chungkitchan/ibm-watson-pipelines-test/archive/refs/heads/main.zip
from ibm_watson_pipelines import WatsonPipelines
import os

output = {
  "model": "Test model",
  "scoret": 0.9
}

print(output)

pipelines_client = WatsonPipelines.from_token(os.environ["USER_ACCESS_TOKEN"])

res = pipelines_client.store_results(output)
print(res)

```