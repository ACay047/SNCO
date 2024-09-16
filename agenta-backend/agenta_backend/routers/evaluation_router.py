import random
import logging
from typing import Any, List, Optional

from fastapi.responses import JSONResponse
from fastapi import HTTPException, Request, status, Response, Query

from agenta_backend.models import converters

from agenta_backend.tasks.evaluations import evaluate
from agenta_backend.utils.common import APIRouter, isCloudEE
from agenta_backend.models.api.evaluation_model import (
    Evaluation,
    EvaluationScenario,
    NewEvaluation,
    DeleteEvaluation,
)
from agenta_backend.services.evaluator_manager import (
    check_ai_critique_inputs,
)
from agenta_backend.services import evaluation_service, db_manager, app_manager

if isCloudEE():
    from agenta_backend.commons.models.shared_models import Permission
    from agenta_backend.commons.utils.permissions import check_action_access


router = APIRouter()
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@router.get(
    "/by_resource/",
    response_model=List[str],
)
async def fetch_evaluation_ids(
    app_id: str,
    resource_type: str,
    request: Request,
    resource_ids: List[str] = Query(None),
):
    """Fetches evaluation ids for a given resource type and id.

    Arguments:
        app_id (str): The ID of the app for which to fetch evaluations.
        resource_type (str): The type of resource for which to fetch evaluations.
        resource_ids List[ObjectId]: The IDs of resource for which to fetch evaluations.

    Raises:
        HTTPException: If the resource_type is invalid or access is denied.

    Returns:
        List[str]: A list of evaluation ids.
    """
    try:
        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                project_id=request.state.project_id,
                permission=Permission.VIEW_EVALUATION,
            )
            logger.debug(
                f"User has permission to get single evaluation: {has_permission}"
            )
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )
        evaluations = await db_manager.fetch_evaluations_by_resource(
            resource_type, request.state.project_id, resource_ids
        )
        return list(map(lambda x: str(x.id), evaluations))
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/", response_model=List[Evaluation], operation_id="create_evaluation")
async def create_evaluation(
    payload: NewEvaluation,
    request: Request,
):
    """Creates a new comparison table document
    Raises:
        HTTPException: _description_
    Returns:
        _description_
    """
    try:
        app = await db_manager.fetch_app_by_id(
            app_id=payload.app_id, project_id=request.state.project_id
        )
        if app is None:
            raise HTTPException(status_code=404, detail="App not found")

        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                project_id=request.state.project_id,
                permission=Permission.CREATE_EVALUATION,
            )
            logger.debug(f"User has permission to create evaluation: {has_permission}")
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        success, response = await check_ai_critique_inputs(
            payload.evaluators_configs, payload.lm_providers_keys
        )
        if not success:
            return response

        evaluations = []

        for variant_id in payload.variant_ids:
            evaluation = await evaluation_service.create_new_evaluation(
                app_id=payload.app_id,
                project_id=request.state.project_id,
                variant_id=variant_id,
                testset_id=payload.testset_id,
            )

            evaluate.delay(
                app_id=payload.app_id,
                project_id=request.state.project_id,
                variant_id=variant_id,
                evaluators_config_ids=payload.evaluators_configs,
                testset_id=payload.testset_id,
                evaluation_id=evaluation.id,
                rate_limit_config=payload.rate_limit.dict(),
                lm_providers_keys=payload.lm_providers_keys,
            )
            evaluations.append(evaluation)

        # Update last_modified_by app information
        await app_manager.update_last_modified_by(
            user_uid=request.state.user_id,
            object_id=payload.app_id,
            object_type="app",
            project_id=request.state.project_id,
        )
        logger.debug("Successfully updated last_modified_by app information")

        return evaluations
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail="columns in the test set should match the names of the inputs in the variant",
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        status_code = e.status_code if hasattr(e, "status_code") else 500  # type: ignore
        raise HTTPException(status_code, detail=str(e))


@router.get("/{evaluation_id}/status/", operation_id="fetch_evaluation_status")
async def fetch_evaluation_status(
    evaluation_id: str,
    request: Request,
):
    """Fetches the status of the evaluation.

    Args:
        evaluation_id (str): the evaluation id
        request (Request): the request object

    Returns:
        (str): the evaluation status
    """

    try:
        evaluation = await db_manager.fetch_evaluation_by_id(
            evaluation_id, request.state.project_id
        )
        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                object=evaluation,
                permission=Permission.VIEW_EVALUATION,
            )
            logger.debug(
                f"User has permission to fetch evaluation status: {has_permission}"
            )
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        return {"status": evaluation.status}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{evaluation_id}/results/", operation_id="fetch_evaluation_results")
async def fetch_evaluation_results(
    evaluation_id: str,
    request: Request,
):
    """Fetches the results of the evaluation

    Args:
        evaluation_id (str): the evaluation id
        request (Request): the request object

    Returns:
        _type_: _description_
    """

    try:
        evaluation = await db_manager.fetch_evaluation_by_id(
            evaluation_id, project_id=request.state.project_id
        )
        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                object=evaluation,
                permission=Permission.VIEW_EVALUATION,
            )
            logger.debug(
                f"User has permission to get evaluation results: {has_permission}"
            )
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        results = converters.aggregated_result_of_evaluation_to_pydantic(
            evaluation.aggregated_results  # type: ignore
        )
        return {"results": results, "evaluation_id": evaluation_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/{evaluation_id}/evaluation_scenarios/",
    response_model=List[EvaluationScenario],
    operation_id="fetch_evaluation_scenarios",
)
async def fetch_evaluation_scenarios(
    evaluation_id: str,
    request: Request,
):
    """Fetches evaluation scenarios for a given evaluation ID.

    Arguments:
        evaluation_id (str): The ID of the evaluation for which to fetch scenarios.

    Raises:
        HTTPException: If the evaluation is not found or access is denied.

    Returns:
        List[EvaluationScenario]: A list of evaluation scenarios.
    """

    try:
        evaluation = await db_manager.fetch_evaluation_by_id(
            evaluation_id, request.state.project_id
        )
        if not evaluation:
            raise HTTPException(
                status_code=404, detail=f"Evaluation with id {evaluation_id} not found"
            )

        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                object=evaluation,
                permission=Permission.VIEW_EVALUATION,
            )
            logger.debug(
                f"User has permission to get evaluation scenarios: {has_permission}"
            )
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        eval_scenarios = (
            await evaluation_service.fetch_evaluation_scenarios_for_evaluation(
                evaluation_id=str(evaluation.id), project_id=request.state.project_id
            )
        )
        return eval_scenarios

    except Exception as exc:
        import traceback

        traceback.print_exc()
        status_code = exc.status_code if hasattr(exc, "status_code") else 500
        raise HTTPException(status_code=status_code, detail=str(exc))


@router.get("/", response_model=List[Evaluation])
async def fetch_list_evaluations(
    app_id: str,
    request: Request,
):
    """Fetches a list of evaluations, optionally filtered by an app ID.

    Args:
        app_id (Optional[str]): An optional app ID to filter the evaluations.

    Returns:
        List[Evaluation]: A list of evaluations.
    """
    try:
        app = await db_manager.fetch_app_by_id(app_id, request.state.project_id)
        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                object=app,
                permission=Permission.VIEW_EVALUATION,
            )
            logger.debug(
                f"User has permission to get list of evaluations: {has_permission}"
            )
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        return await evaluation_service.fetch_list_evaluations(
            app, request.state.project_id
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        status_code = exc.status_code if hasattr(exc, "status_code") else 500
        raise HTTPException(
            status_code=status_code,
            detail=f"Could not retrieve evaluation results: {str(exc)}",
        )


@router.get(
    "/{evaluation_id}/", response_model=Evaluation, operation_id="fetch_evaluation"
)
async def fetch_evaluation(
    evaluation_id: str,
    request: Request,
):
    """Fetches a single evaluation based on its ID.

    Args:
        evaluation_id (str): The ID of the evaluation to fetch.

    Returns:
        Evaluation: The fetched evaluation.
    """
    try:
        evaluation = await db_manager.fetch_evaluation_by_id(
            evaluation_id, request.state.project_id
        )
        if not evaluation:
            raise HTTPException(
                status_code=404, detail=f"Evaluation with id {evaluation_id} not found"
            )

        if isCloudEE():
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                object_id=evaluation_id,
                object_type="evaluation",
                permission=Permission.VIEW_EVALUATION,
            )
            logger.debug(
                f"User has permission to get single evaluation: {has_permission}"
            )
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        return await converters.evaluation_db_to_pydantic(evaluation)
    except Exception as exc:
        status_code = exc.status_code if hasattr(exc, "status_code") else 500
        raise HTTPException(status_code=status_code, detail=str(exc))


@router.delete("/", response_model=List[str], operation_id="delete_evaluations")
async def delete_evaluations(
    payload: DeleteEvaluation,
    request: Request,
):
    """
    Delete specific comparison tables based on their unique IDs.

    Args:
    delete_evaluations (List[str]): The unique identifiers of the comparison tables to delete.

    Returns:
    A list of the deleted comparison tables' IDs.
    """

    try:
        if isCloudEE():
            evaluation_id = random.choice(payload.evaluations_ids)
            has_permission = await check_action_access(
                user_uid=request.state.user_id,
                object_id=evaluation_id,
                object_type="evaluation",
                permission=Permission.DELETE_EVALUATION,
            )
            logger.debug(f"User has permission to delete evaluation: {has_permission}")
            if not has_permission:
                error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                logger.error(error_msg)
                return JSONResponse(
                    {"detail": error_msg},
                    status_code=403,
                )

        # Update last_modified_by app information
        await app_manager.update_last_modified_by(
            user_uid=request.state.user_id,
            object_id=random.choice(payload.evaluations_ids),
            object_type="evaluation",
            project_id=request.state.project_id,
        )
        logger.debug("Successfully updated last_modified_by app information")

        await evaluation_service.delete_evaluations(
            payload.evaluations_ids, request.state.project_id
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/evaluation_scenarios/comparison-results/",
    response_model=Any,
)
async def fetch_evaluation_scenarios(
    evaluations_ids: str,
    request: Request,
):
    """Fetches evaluation scenarios for a given evaluation ID.

    Arguments:
        evaluation_id (str): The ID of the evaluation for which to fetch scenarios.

    Raises:
        HTTPException: If the evaluation is not found or access is denied.

    Returns:
        List[EvaluationScenario]: A list of evaluation scenarios.
    """
    try:
        evaluations_ids_list = evaluations_ids.split(",")

        if isCloudEE():
            for evaluation_id in evaluations_ids_list:
                has_permission = await check_action_access(
                    user_uid=request.state.user_id,
                    object_id=evaluation_id,
                    object_type="evaluation",
                    permission=Permission.VIEW_EVALUATION,
                )
                logger.debug(
                    f"User has permission to get evaluation scenarios: {has_permission}"
                )
                if not has_permission:
                    error_msg = f"You do not have permission to perform this action. Please contact your organization admin."
                    logger.error(error_msg)
                    return JSONResponse(
                        {"detail": error_msg},
                        status_code=403,
                    )

        eval_scenarios = await evaluation_service.compare_evaluations_scenarios(
            evaluations_ids_list, request.state.project_id
        )

        return eval_scenarios
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
