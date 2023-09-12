# IBM Watson Pipelines Python Client

This package provides various utilities for working with IBM Watson Pipelines. 
Its primary usage is to enable users to store artifact results of a notebook run.


## Usage

### Construction

`WatsonPipelines` client is constructed from IAM APIKEY, which can be provided
in a few ways:

* explicitly:

  ```python
  from ibm_watson_pipelines import WatsonPipelines
  
  client = WatsonPipelines(apikey)
  # or
  client = WatsonPipelines.from_apikey(apikey)
  # or
  client = WatsonPipelines.from_token(token)
  ```

* implicitly:

  ```bash
  APIKEY=...
  export APIKEY
  ```
  or
  ```bash
  USER_ACCESS_TOKEN=...
  export USER_ACCESS_TOKEN
  ```

  ```python
  from ibm_watson_pipelines import WatsonPipelines

  # use APIKEY
  client = WatsonPipelines.from_apikey()

  # use USER_ACCESS_TOKEN
  client = WatsonPipelines.from_token()

  # try APIKEY, if absent then USER_ACCESS_TOKEN:
  client = WatsonPipelines()
  # or
  client = WatsonPipelines.new_instance()
  ```

All of the above may also define `service_name` and `url`.

The exact procedure of deciding which authentication method to use:
1. If `from_apikey` or `from_token` is used, the method is forced.
2. If constructor is used but either `apikey` or `bearer_token` argument
  was provided, that method will be forced (if both are present,
  an overloading error will be raised). Note that providing a nameless
  argument is equivalent to providing `apikey`.
3. If constructor or `new_instance` is used, `APIKEY` env-var is used.
4. If constructor or `new_instance` is used, but `APIKEY` env-var is not
   present, `USER_ACCESS_TOKEN` env-var is used.
5. If none of the above matches, an error is returned.


### Usage in Python notebooks

Notebooks run in IBM Watson Pipelines get inputs and expose
outputs as a node:

```
{
  "id": ...,
  "type": "execution_node",
  "op": "run_container",
  "app_data": {
    "pipeline_data": {
      "name": ...,
      "config": {
        "link": {
          "component_id_ref": "run-notebook"
        }
      },
      "inputs": [
        ...,
        {
          "name": "model_name",
          "group": "env_variables",
          "type": "String",
          "value_from": ...
        }
      ],
      "outputs": [
        {
          "name": "trained_model",
          "group": "output_variables",
          "type": {
            "CPDPath": {
              "path_type": "resource",
              "resource_type": "asset",
              "asset_type": "wml_model"
            }
          }
        }
      ]
    }
  },
  ...
}
```

Inside of the notebook, inputs are available as environmental
variables:

```python
model_name = os.environ['model_name']
```

Outputs are exposed using sdk method, `store_results`:

```python
client = WSPipelines.from_apikey(...)
client.store_results({
  "trained_model": ... // cpd path to the trained model
})
```


### Extracting credentials

On public cloud, this client provides a method for easy retrieval of WML
instance credentials and scope storage credentials:

```python
client.get_wml_credentials() # the scope passed in notebook
# or
client.get_wml_credentials("cpd:///projects/123456789")
```
```python
client.get_storage_credentials() # the scope passed in notebook
# or
client.get_storage_credentials("cpd:///projects/123456789")
```

Note how the result will vary depending on the authentication method
used to create the client.


### CPD-Path manipulation

CPD-Path parsing is manipulation is also supported:

```python
from ibm_watson_pipelines import CpdScope, WatsonPipelines

client = WatsonPipelines.from_apikey()

scope = CpdScope.from_string("cpd:///projects/123456789")

assert scope.scope_type() == "projects"
assert scope.scope_id() == "123456789"

client.get_wml_credentials(scope)
```

Different kinds of CPD-Paths will have different properties, providing the same
interface across scopes, resource and file paths:

```python
from ibm_watson_pipelines import CpdPath

scope_file_path = CpdPath.from_string("cpd:///projects/123456789/files/abc/def")
assert scope_file_path.scope_type() == "projects"
assert scope_file_path.scope_id() == "123456789"
assert scope_file_path.file_path() == "/abc/def"

connection_path = CpdPath.from_string("cpd:///projects/123456789/connections/3141592")
assert connection_path.scope_type() == "projects"
assert connection_path.scope_id() == "123456789"
assert connection_path.resource_type() == "connections"
assert connection_path.resource_id() == "3141592"

connection_file_path = CpdPath.from_string("cpd:///projects/123456789/connections/3141592/files/~/abc/def")
assert connection_file_path.scope_type() == "projects"
assert connection_file_path.scope_id() == "123456789"
assert connection_file_path.resource_type() == "connections"
assert connection_file_path.resource_id() == "3141592"
assert connection_file_path.bucket_name() == "~"
assert connection_file_path.file_path() == "/abc/def"
```

...additionally, for non-scope paths the scope can be extracted, if present:

```python
from ibm_watson_pipelines import CpdPath

scope_path = CpdPath.from_string("cpd:///projects/123456789")
connection_path = CpdPath.from_string("cpd:///projects/123456789/connections/3141592")
assert connection_path.scope() == scope_path
```

### Custom components for use in the pipeline
A custom pipeline component executes a script you write. You can use custom components to share reusable scripts between pipelines.

You create custom components as project assets. You can then use the components in pipelines you create in that project. You can create as many custom components for pipelines as needed. Currently, to create a custom component you must create one programmatically, using a Python function.

#### Creating a component as a project asset
To create a custom component, use the Python client to authenticate with IBM Watson Pipelines, code the component, then publish the component to the specified project. After it is available in the project, you can assign it to a node in a pipeline and run it as part of a pipeline flow.

This example demonstrates the process of publishing a component that adds two numbers together.

Publish a function as a component with the latest Python client. Run the following code in a Jupyter notebook in a project of your Cloud Pak for Data.

```python
# Install libraries
! pip install ibm-watson-pipelines==1.0.0

# Authentication
from ibm_watson_pipelines import WatsonPipelines

apikey = ''
service_url = 'your_host_url'
project_id = 'your_project_id'

client = WatsonPipelines.from_apikey(apikey, url=service_url)

# Define the function of the component

# If you define the input parameters, users are required to 
# input them in the UI

def add_two_numbers(a: int, b: int) -> int:
  print('Adding numbers: {} + {}.'.format(a, b))
  return a + b + 10
  
# Other possible functions might be sending a Slack message,
# or listing directories in a storage volume, and so on.

# Publish the component    
client.publish_component(
  name='Add numbers', # Appears in UI as component name 
  func=add_two_numbers,
  description='Custom component adding numbers', # Appears in UI as component description 
  project_id=project_id,
  overwrite=True, # Overwrites an existing component with the same name 
)
```

#### Manage pipeline components
- list components from a project:
```python
client.get_components(project_id=project_id)
```
- get a component by ID:
```python
client.get_component(project_id=project_id, component_id=component_id)
```
- get a component by name:
```python
client.get_component(project_id=project_id, name=component_name)	
```
- publish a new component:
```python
client.publish_component(component name)
```
- delete a component by ID:
```python
client.delete_component(project_id=project_id, component_id=component_id)
```
