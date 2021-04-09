"""This module defines functions and classes for representing Job objects."""

from __future__ import annotations

import logging
import typing
import warnings
from dataclasses import dataclass, field

from monty.json import MSONable, jsanitize

from activities.core.reference import Reference, ReferenceFallback
from activities.utils.uuid import suuid

if typing.TYPE_CHECKING:
    from typing import Any, Callable, Dict, Hashable, List, Optional, Tuple, Type, Union

    from pydantic.main import BaseModel

    import activities

logger = logging.getLogger(__name__)

__all__ = ["job", "Job", "Response", "JobConfig", "store_output"]


@dataclass
class JobConfig(MSONable):
    """
    The configuration parameters for a job.

    Parameters
    ----------
    resolve_references
        Whether to resolve any references before the job function is executed.
        If ``False`` the unresolved reference objects will be passed into the function
        call.
    on_missing_references
        What to do if the references cannot be resolved. The default is to throw an
        error.
    manager_config
        The configuration settings to control the manager execution.
    expose_store
        Whether to expose the store in :obj:`.CURRENT_JOB`` when the job is running.

    Returns
    -------
    JobConfig
        A :obj:`JobConfig` object.
    """

    resolve_references: bool = True
    on_missing_references: ReferenceFallback = ReferenceFallback.ERROR
    manager_config: dict = field(default_factory=dict)
    expose_store: bool = False


def job(method: Optional[Callable] = None, **job_kwargs):
    """
    Wraps a function to produce a :obj:`Job`.

    :obj:`Job` objects are delayed function calls that can be used in an
    :obj:`Activity`. A job is a composed of the function name and source and any
    arguments for the function. This decorator makes it simple to create
    job objects directly from a function definition. See the examples for more details.

    Parameters
    ----------
    method
        A function to wrap. This should not be specified directly and is implied
        by the decorator.
    **job_kwargs
        Other keyword arguments that will get passed to the :obj:`Job` init method.

    Examples
    --------
    >>> @job
    ... def print_message():
    ...     print("I am a Job")
    >>> print_job = print_message()
    >>> type(print_job)
    <class 'activities.core.job.Job'>
    >>> print_job.function_source
    '__main__'
    >>> print_job.function_name
    'print_message'

    Jobs can have required and optional parameters.

    >>> @job
    ... def print_sum(a, b=0):
    ...     return print(a + b)
    >>> print_sum_job = print_sum(1, 2)
    >>> print_sum_job.function_args
    (1, )
    >>> print_sum_job.function_kwargs
    {"b": 2}

    If the function returns a value it can be referenced using the ``output``
    attribute of the job.

    >>> @job
    ... def add(a, b):
    ...     return a + b
    >>> add_task = add(1, 2)
    >>> add_task.output
    Reference('abeb6f48-9b34-4698-ab69-e4dc2127ebe9')

    .. Note::
        Because the task has not yet been run, the output value is :obj:`Reference`
        object. References are automatically converted to their computed values
        (resolved) when the task runs.

    If a dictionary of values is returned, the values can be indexed in the usual
    way.

    >>> @job
    ... def compute(a, b):
    ...     return {"sum": a + b, "product": a * b}
    >>> compute_task = compute(1, 2)
    >>> compute_task.output["sum"]
    Reference('abeb6f48-9b34-4698-ab69-e4dc2127ebe9', 'sum')

    .. Warning::
        If an output is indexed incorrectly, for example by trying to access a key that
        doesn't exist, this error will only be raised when the Job is executed.

    Jobs can return :obj:`.Response` objects that control the activity execution flow.
    For example, to replace the current jub with another job, ``replace`` can be used.

    >>> from activities import Response
    >>> @job
    ... def replace(a, b):
    ...     new_job = compute(a, b)
    ...     return Response(restart=new_job)

    See Also
    --------
    Job, .Activity, .Response
    """

    def decorator(func):
        from functools import wraps

        @wraps(func)
        def get_task(*args, **kwargs) -> Job:
            if len(args) > 0 and isinstance(args[0], MSONable):
                # this is an MSONable class (likely a maker function)
                func_source = args[0]
                args = args[1:]
            else:
                func_source = func.__module__

            return Job(
                function_source=func_source,
                function_name=func.__name__,
                function_args=args,
                function_kwargs=kwargs,
                **job_kwargs,
            )

        return get_task

    # See if we're being called as @job or @job().
    if method is None:
        # We're called with parens.
        return decorator

    # We're called as @job without parens.
    return decorator(method)


@dataclass
class Job(MSONable):
    """
    A :obj:`Job` is a delayed function call that can be used in an :obj:`.Activity`.

    In general, one should not create :obj:`Job` objects directly but instead use
    the :obj:`job` decorator on a function. Any calls to a decorated function will
    return an :obj:`Job` object.

    Parameters
    ----------
    function_source
        The source of the function. Can be ``"builtins"`` for builtin functions,
        a module name, or an :obj:`.MSONable` class instance.
    function_name
        The function name.
    function_args
        The positional arguments to the function call.
    function_kwargs
        The keyword arguments to the function call.
    output_schema
        A :obj:`Schema` object that defines the schema of the output.
    uuid
        A unique identifier for the job. Generated automatically.
    index
        The index of the job (number of times the job has been replaced).
    name
        The name of the job. If not set it will be determined from ``function_source``
        and ``function_name``.
    metadata
        A dictionary of information that will get stored alongside the job output.
    config
        The config setting for the job.
    host
        The UUID of the host activity.

    Attributes
    ----------
    output
        The output of the job. This is a reference to the future job output and
        can be used as the input to other jobs or activities.

    Returns
    -------
    Job
        A job.

    Examples
    --------
    Builtin functions such as :obj:`print` can be specified using the ``builtins``
    module.

    >>> print_task = Job(function=("builtins", "print"), args=("I am a job", ))

    Other functions should specify the full module path.

    >>> Job(function=("os.path", "join"), args=("folder", "filename.txt"))

    To use custom functions in a job, the functions should be importable (i.e. not
    defined in another function). For example, if the following function is defined
    in the ``my_package`` module.

    >>> def add(a, b):
    ...     return a + b
    >>> add_job = Job(function=("my_package", "add"), args=(1, 2))

    More details are given in the :obj:`job` decorator docstring.

    See Also
    --------
    job, Response, .Activity
    """

    function_source: Union[str, MSONable]
    function_name: str
    function_args: Tuple[Any, ...] = field(default_factory=tuple)
    function_kwargs: Dict[str, Any] = field(default_factory=dict)
    output_schema: Optional[Type[BaseModel]] = None
    uuid: str = field(default_factory=suuid)
    index: int = 1
    name: Optional[str] = None
    data: Union[bool, str, Type[MSONable], List[Union[str, Type[MSONable]]]] = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    config: JobConfig = field(default_factory=JobConfig)
    host: Optional[str] = None
    output: Reference = field(init=False)

    def __post_init__(self):
        from activities.utils.find import contains_activity_or_job

        self.output = Reference(self.uuid, output_schema=self.output_schema)
        if self.name is None:
            if self.is_msonable_job and hasattr(self.function_source, "name"):
                self.name = self.function_source.name
            else:
                self.name = self.function_name

        # check to see if job or activity is included in the job args
        # this is a possible situation but likely a mistake
        all_args = tuple(self.function_args) + tuple(self.function_kwargs.values())
        if contains_activity_or_job(all_args):
            warnings.warn(
                f"Job '{self.name}' contains an Activity or Job as an input. "
                f"Usually inputs should be the output of a Job or an Activity (e.g. "
                f"job.output). If this message is unexpected then double check the "
                f"inputs to your Job."
            )

    @property
    def is_msonable_job(self) -> bool:
        """
        Whether the job belongs to a :obj:`.MSONable` class.

        Returns
        -------
        bool
            Whether the job function is on an ``MSONable`` class.
        """
        return isinstance(self.function_source, MSONable)

    @property
    def input_references(self) -> Tuple[activities.Reference, ...]:
        """
        Find :obj:`.Reference` objects in the job inputs.

        Returns
        -------
        tuple(Reference, ...)
            The references in the inputs to the job.
        """
        from activities.core.reference import find_and_get_references

        references = set()
        for arg in tuple(self.function_args) + tuple(self.function_kwargs.values()):
            references.update(find_and_get_references(arg))

        return tuple(references)

    @property
    def graph(self):
        from networkx import DiGraph

        edges = []
        for uuid, refs in self.input_references_grouped.items():
            print(refs)
            properties = [
                ".".join(map(str, ref.attributes)) for ref in refs if ref.attributes
            ]
            properties = properties if len(properties) > 0 else "output"
            edges.append((uuid, self.uuid, {"properties": properties}))

        graph = DiGraph()
        graph.add_node(self.uuid, job=self, label=self.name)
        graph.add_edges_from(edges)
        return graph

    def run(self, store: activities.ActivityStore) -> "Response":
        """
        Run the job.

        If the job has inputs that are :obj:`.Reference` objects, then they will need
        to be resolved before the job can run. See the docstring for
        :obj:`.Reference.resolve()` for more details.

        Parameters
        ----------
        store
            A maggma store to use for resolving references and storing job outputs.

        Returns
        -------
        Response
            A the response of the job, containing the outputs, and other settings
            that determine the activity execution.

        Raises
        ------
        ValueError
            If the job function cannot be imported.

        See Also
        --------
        Response, .Reference
        """
        import inspect
        from datetime import datetime
        from importlib import import_module

        from activities import CURRENT_JOB

        index_str = f", {self.index}" if self.index != 1 else ""
        logger.info(f"Starting job - {self.name} ({self.uuid}{index_str})")
        CURRENT_JOB.job = self

        if self.config.expose_store:
            CURRENT_JOB.store = store

        if self.config.resolve_references:
            self.resolve_args(store=store)

        if self.is_msonable_job:
            function = getattr(self.function_source, self.function_name)
            function = inspect.unwrap(function)
            response: Response = function(
                self.function_source, *self.function_args, **self.function_kwargs
            )

        else:
            module = import_module(self.function_source)
            function = getattr(module, self.function_name, None)

            if function is None:
                raise ValueError(f"Could not import {self.function_name} from {module}")
            function = inspect.unwrap(function)
            response: Response = function(*self.function_args, **self.function_kwargs)

        if not isinstance(response, Response):
            response = Response.from_job_returns(response, self.output_schema)

        if response.restart is not None:
            response.restart = prepare_restart(response.restart, self)

        save = "output" if self.data is True else self.data
        data = {
            "uuid": self.uuid,
            "index": self.index,
            "output": jsanitize(response.output, strict=True),
            "completed_at": datetime.now().isoformat(),
            "metadata": self.metadata,
        }
        store.update(data, key=["uuid", "index"], save=save)

        CURRENT_JOB.reset()
        logger.info(f"Finished job - {self.name} ({self.uuid}{index_str})")
        return response

    def set_uuid(self, uuid: str):
        self.uuid = uuid
        self.output = self.output.set_uuid(uuid)

    def resolve_args(
        self,
        store: activities.ActivityStore,
        on_missing: ReferenceFallback = ReferenceFallback.ERROR,
        inplace: bool = True,
    ) -> "Job":
        """
        Resolve any :obj:`.Reference` objects in the input arguments.

        See the docstring for :obj:`.Reference.resolve()` for more details.

        Parameters
        ----------
        store
            A maggma store to use for resolving references.
        on_missing
            What to do if the reference cannot be resolved. See the docstring
            for :obj:`.Reference.resolve` for the available options.
            Whether to raise an error if a reference cannot be resolved.
        inplace
            Update the arguments of the current job or return a new job object.

        Returns
        -------
        Job
            A job with the references resolved.
        """
        from copy import deepcopy

        from activities.core.reference import find_and_resolve_references

        resolved_args = find_and_resolve_references(
            self.function_args,
            store=store,
            on_missing=on_missing,
        )
        resolved_kwargs = find_and_resolve_references(
            self.function_kwargs,
            store=store,
            on_missing=on_missing,
        )
        resolved_args = tuple(resolved_args)

        if inplace:
            self.function_args = resolved_args
            self.function_kwargs = resolved_kwargs
            return self

        new_job = deepcopy(self)
        new_job.function_args = resolved_args
        new_job.function_kwargs = resolved_kwargs
        return new_job

    def update_kwargs(
        self,
        update: Dict[str, Any],
        name_filter: Optional[str] = None,
        function_filter: Optional[Callable] = None,
        dict_mod: bool = False,
    ):
        from types import BuiltinFunctionType, FunctionType, MethodType

        from activities.utils.dict_mods import apply_mod

        if isinstance(function_filter, (FunctionType, BuiltinFunctionType, MethodType)):
            function_filter = (function_filter.__module__, function_filter.__name__)

        elif not isinstance(function_filter, (tuple, type(None))):
            raise ValueError("Unrecognised type of function filter.")

        if name_filter is not None and name_filter not in self.name:
            return

        if function_filter is not None and function_filter != (
            self.function_source,
            self.function_name,
        ):
            return

        # if we get to here then we pass all the filters
        if dict_mod:
            apply_mod(update, self.function_kwargs)
        else:
            self.function_kwargs.update(update)

    def update_maker_kwargs(
        self,
        update: Dict[str, Any],
        name_filter: Optional[str] = None,
        class_filter: Optional[Type[activities.Maker]] = None,
        nested: bool = True,
        dict_mod: bool = False,
    ):
        if self.is_msonable_job:
            self.function_source = self.function_source.update_kwargs(
                update,
                name_filter=name_filter,
                class_filter=class_filter,
                nested=nested,
                dict_mod=dict_mod,
            )

    @property
    def input_uuids(self) -> Tuple[str, ...]:
        return tuple([ref.uuid for ref in self.input_references])

    @property
    def input_references_grouped(self) -> Dict[str, Tuple[Reference, ...]]:
        from collections import defaultdict

        groups = defaultdict(set)
        for ref in self.input_references:
            groups[ref.uuid].add(ref)

        return {k: tuple(v) for k, v in groups.items()}


@dataclass
class Response:
    """
    The :obj:`Response` contains the output, detours, and stop commands of a job.

    Parameters
    ----------
    output
        The job output.
    detour
        An activity or job to detour to.
    restart
        An activity or job to replace the current job.
    store
        Data to be stored by the activity manager.
    stop_children
        Stop any children of the current activity.
    stop_activities
        Stop executing all remaining jobs.
    """

    output: Optional[Any] = None
    restart: Optional[Union[activities.Activity, Job, List[Job]]] = None
    detour: Optional[Union[activities.Activity, Job, List[Job]]] = None
    addition: Optional[Union[activities.Activity, Job, List[Job]]] = None
    stored_data: Optional[Dict[Hashable, Any]] = None
    stop_children: bool = False
    stop_activities: bool = False

    @classmethod
    def from_job_returns(
        cls,
        job_returns: Optional[Any],
        output_schema: Optional[Type[BaseModel]] = None,
    ) -> Response:
        """
        Generate a :obj:`Response` from the outputs of a :obj:`Job`.

        Parameters
        ----------
        job_returns
            The outputs of a job. Should be a single or list of :obj:`Outputs`,
            :obj:`Store`, :obj:`Detour`, :obj:`Restart`, or :obj:`Stop` objects. Only
            one of each type of object is supported.

            .. Warning::
                :obj:`Detour` and :obj:`Outputs` objects should not be specified
                simultaneously. The outputs of the detour activity will be used instead.

        output_schema
            The outputs class associated with the job. Used to enforce a schema for the
            outputs. Currently, only a warning will be given if the job outputs do not
            match the expected outputs class.

        Returns
        -------
        Response
            The job response controlling the data to store and activity execution
            options.

        Raises
        ------
        ValueError
            If the job returns type ares not :obj:`Outputs`, :obj:`Store`, :obj:`Detour`,
            :obj:`Restart`, or :obj:`Stop` objects.
        ValueError
            If more than one of the same return type is given.

        See Also
        --------
        .Outputs, Store, Detour, Restart, Stop
        """
        if isinstance(job_returns, Response):
            if job_returns.restart is not None:
                # only apply output schema if there is no restart.
                job_returns.output = apply_schema(job_returns.output)

            return job_returns

        if isinstance(job_returns, (list, tuple)):
            # check that a Response object is not given as one of many outputs
            for r in job_returns:
                if isinstance(r, Response):
                    raise ValueError(
                        "Response cannot be returned in combination with other "
                        "outputs."
                    )

        return cls(output=apply_schema(job_returns, output_schema))


def apply_schema(output: Any, schema: Optional[Type[BaseModel]]):
    if schema is None or isinstance(output, schema):
        return output

    if output is None:
        raise ValueError(f"Expected output of type {schema.__name__} but got no output")

    if not isinstance(output, Dict):
        raise ValueError(
            f"Expected output to be {schema.__name__} or dict but got output type "
            f"of {type(output).__name__}."
        )

    return schema(**output)


@job(
    config=JobConfig(
        resolve_references=False, on_missing_references=ReferenceFallback.NONE
    )
)
def store_output(outputs: Any):
    return outputs


def prepare_restart(
    restart: Union[activities.Activity, Job, List[Job]],
    current_job: Job,
):
    from activities.core.activity import Activity

    if isinstance(restart, (list, tuple)):
        restart = Activity(jobs=restart)

    if isinstance(restart, Activity) and restart.output is not None:
        # add a job with same uuid as the current job to store the outputs of the
        # activity; this job will inherit the metadata and output schema of the current
        # job
        store_output_job = store_output(restart.output)
        store_output_job.config.manager_config = current_job.config.manager_config
        store_output_job.set_uuid(current_job.uuid)
        store_output_job.index = current_job.index + 1
        store_output_job.metadata = current_job.metadata
        store_output_job.output_schema = current_job.output_schema
        restart.jobs.append(store_output_job)

    else:
        # restart is a single Job
        restart.set_uuid(current_job.uuid)
        restart.index = current_job.index + 1

        metadata = restart.metadata
        metadata.update(current_job.metadata)
        restart.metadata = metadata

        if not restart.output_schema:
            restart.output_schema = current_job.output_schema

    return restart