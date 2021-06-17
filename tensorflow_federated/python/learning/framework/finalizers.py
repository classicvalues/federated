# Copyright 2021, The TensorFlow Federated Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Abstractions for finalization in learning algorithms."""

from tensorflow_federated.python.common_libs import structure
from tensorflow_federated.python.core.impl.types import placements
from tensorflow_federated.python.core.templates import errors
from tensorflow_federated.python.core.templates import measured_process
from tensorflow_federated.python.learning import model_utils


class ModelWeightsTypeError(TypeError):
  """`TypeError` for incorrect container of model weights."""


class FinalizerResultTypeError(TypeError):
  """`TypeError` for finalizer not updating model weights as expected."""


class FinalizerProcess(measured_process.MeasuredProcess):
  """A stateful process for finalization of a round of training.

  A `FinalizerProcess` is a `tff.templates.MeasuredProcess` that formalizes the
  type signature of `initialize_fn` and `next_fn` for the work performed by
  server in a learning process after aggregating model updates from clients.

  The `initialize_fn` and `next_fn` must have the following type signatures:
  ```
    - initialize_fn: ( -> S@SERVER)
    - next_fn: (<S@SERVER,
                 ModelWeights(TRAINABLE, NON_TRAINABLE)@SERVER,
                 WEIGHT_UPDATE@SERVER>
                ->
                <state=S@SERVER,
                 result=ModelWeights(TRAINABLE, NON_TRAINABLE)@SERVER,
                 measurements=M@SERVER>)
  ```

  `FinalizerProcess` requires `next_fn` with a second and a third input
  argument, which are both placed at `SERVER`. The second argument represents
  the model weights to be updated. It must be of a type matching
  `tff.learning.ModelWeights`. The third input argument represents information
  to be used to update the model weights, often matching the type signature of
  `TRAINABLE`.

  The `result` field of the returned `tff.templates.MeasuredProcessOutput` must
  be placed at `SERVER`, be of type matching that of second input argument,
  which represents the updated ("finalized") model weights.
  """

  def __init__(self, initialize_fn, next_fn):
    super().__init__(initialize_fn, next_fn, next_is_multi_arg=True)

    if not initialize_fn.type_signature.result.is_federated():
      raise errors.TemplateNotFederatedError(
          f'Provided `initialize_fn` must return a federated type, but found '
          f'return type:\n{initialize_fn.type_signature.result}\nTip: If you '
          f'see a collection of federated types, try wrapping the returned '
          f'value in `tff.federated_zip` before returning.')
    next_types = (
        structure.flatten(next_fn.type_signature.parameter) +
        structure.flatten(next_fn.type_signature.result))
    if not all([t.is_federated() for t in next_types]):
      offending_types = '\n- '.join(
          [t for t in next_types if not t.is_federated()])
      raise errors.TemplateNotFederatedError(
          f'Provided `next_fn` must be a *federated* computation, that is, '
          f'operate on `tff.FederatedType`s, but found\n'
          f'next_fn with type signature:\n{next_fn.type_signature}\n'
          f'The non-federated types are:\n {offending_types}.')

    if initialize_fn.type_signature.result.placement != placements.SERVER:
      raise errors.TemplatePlacementError(
          f'The state controlled by an `FinalizerProcess` must be placed at '
          f'the SERVER, but found type: {initialize_fn.type_signature.result}.')
    # Note that state of next_fn being placed at SERVER is now ensured by the
    # assertions in base class which would otherwise raise
    # TemplateStateNotAssignableError.

    next_fn_param = next_fn.type_signature.parameter
    if not next_fn_param.is_struct():
      raise errors.TemplateNextFnNumArgsError(
          f'The `next_fn` must have exactly two input arguments, but found '
          f'the following input type which is not a Struct: {next_fn_param}.')
    if len(next_fn_param) != 3:
      next_param_str = '\n- '.join([str(t) for t in next_fn_param])
      raise errors.TemplateNextFnNumArgsError(
          f'The `next_fn` must have exactly three input arguments, but found '
          f'{len(next_fn_param)} input arguments:\n{next_param_str}')
    model_weights_param = next_fn_param[1]
    update_from_clients_param = next_fn_param[2]
    if model_weights_param.placement != placements.SERVER:
      raise errors.TemplatePlacementError(
          f'The second input argument of `next_fn` must be placed at SERVER '
          f'but found {model_weights_param}.')
    if (not model_weights_param.member.is_struct_with_python() or
        model_weights_param.member.python_container
        is not model_utils.ModelWeights):
      raise ModelWeightsTypeError(
          f'The second input argument of `next_fn` must have the '
          f'`tff.learning.ModelWeights` container but found '
          f'{model_weights_param}')
    if update_from_clients_param.placement != placements.SERVER:
      raise errors.TemplatePlacementError(
          f'The third input argument of `next_fn` must be placed at SERVER '
          f'but found {update_from_clients_param}.')

    next_fn_result = next_fn.type_signature.result
    if next_fn_result.result.placement != placements.SERVER:
      raise errors.TemplatePlacementError(
          f'The "result" attribute of the return type of `next_fn` must be '
          f'placed at SERVER, but found {next_fn_result.result}.')
    if not model_weights_param.member.is_assignable_from(
        next_fn_result.result.member):
      raise FinalizerResultTypeError(
          f'The second input argument of `next_fn` must match the "result" '
          f'attribute of the return type of `next_fn`. Found:\n'
          f'Second input argument: {next_fn_param[1].member}\n'
          f'Result attribute: {next_fn_result.result.member}.')
    if next_fn_result.measurements.placement != placements.SERVER:
      raise errors.TemplatePlacementError(
          f'The "measurements" attribute of return type of `next_fn` must be '
          f'placed at SERVER, but found {next_fn_result.measurements}.')
