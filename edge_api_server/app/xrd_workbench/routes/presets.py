from fastapi import APIRouter, HTTPException, Request

from ..core.errors import RevisionConflict
from ..core.presets import validate_preset


def build_preset_router(store):
    """Routes for settings presets. `store` is the backend's `presets` (see xrd.storage)."""
    router = APIRouter()

    @router.get('/api/presets')
    def list_presets():
        with store.lock:
            return store.list()

    @router.post('/api/presets')
    async def save_preset(request: Request):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, '요청 형식을 확인하세요.') from exc
        if not isinstance(payload, dict) or type(payload.get('overwrite', False)) is not bool:
            raise HTTPException(422, '요청 형식을 확인하세요.')
        try:
            clean = validate_preset(payload.get('name'), payload.get('settings'))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            with store.lock:
                return store.save(clean, overwrite=payload.get('overwrite', False))
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete('/api/presets/{preset_id}')
    def delete_preset(preset_id: str):
        try:
            with store.lock:
                deleted = store.delete(preset_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        if not deleted:
            raise HTTPException(404, '프리셋을 찾지 못했습니다.')
        return {'deleted': preset_id}

    return router
