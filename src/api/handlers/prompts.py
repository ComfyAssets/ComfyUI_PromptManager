"""Prompt management API handlers."""

from aiohttp import web
import json
import sqlite3
from typing import Dict, Any, List
from pathlib import Path


class PromptHandlers:
    """Handles all prompt-related API endpoints."""
    
    def __init__(self, db_manager):
        """Initialize with database manager."""
        self.db_manager = db_manager
    
    async def list_prompts(self, request) -> web.Response:
        """GET /api/prompt_manager/prompts - List all prompts."""
        try:
            # Get query parameters for filtering/pagination
            limit = int(request.query.get('limit', 100))
            offset = int(request.query.get('offset', 0))
            search = request.query.get('search', '')
            
            prompts = self.db_manager.get_prompts(
                limit=limit,
                offset=offset,
                search=search
            )
            
            return web.json_response({
                'success': True,
                'prompts': prompts,
                'total': len(prompts)
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def get_prompt(self, request) -> web.Response:
        """GET /api/prompt_manager/prompts/{id} - Get specific prompt."""
        try:
            prompt_id = int(request.match_info['id'])
            prompt = self.db_manager.get_prompt(prompt_id)
            
            if not prompt:
                return web.json_response({
                    'success': False,
                    'error': 'Prompt not found'
                }, status=404)
            
            return web.json_response({
                'success': True,
                'prompt': prompt
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def create_prompt(self, request) -> web.Response:
        """POST /api/prompt_manager/prompts - Create new prompt."""
        try:
            data = await request.json()
            
            # Validate required fields
            if 'positive' not in data:
                return web.json_response({
                    'success': False,
                    'error': 'Missing required field: positive'
                }, status=400)
            
            prompt_id = self.db_manager.create_prompt(
                positive=data.get('positive', ''),
                negative=data.get('negative', ''),
                metadata=data.get('metadata', {}),
                images=data.get('images', [])
            )
            
            return web.json_response({
                'success': True,
                'id': prompt_id,
                'message': 'Prompt created successfully'
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def update_prompt(self, request) -> web.Response:
        """PUT /api/prompt_manager/prompts/{id} - Update prompt."""
        try:
            prompt_id = int(request.match_info['id'])
            data = await request.json()
            
            success = self.db_manager.update_prompt(
                prompt_id=prompt_id,
                positive=data.get('positive'),
                negative=data.get('negative'),
                metadata=data.get('metadata')
            )
            
            if not success:
                return web.json_response({
                    'success': False,
                    'error': 'Prompt not found'
                }, status=404)
            
            return web.json_response({
                'success': True,
                'message': 'Prompt updated successfully'
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def delete_prompt(self, request) -> web.Response:
        """DELETE /api/prompt_manager/prompts/{id} - Delete prompt."""
        try:
            prompt_id = int(request.match_info['id'])
            success = self.db_manager.delete_prompt(prompt_id)
            
            if not success:
                return web.json_response({
                    'success': False,
                    'error': 'Prompt not found'
                }, status=404)
            
            return web.json_response({
                'success': True,
                'message': 'Prompt deleted successfully'
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def search_prompts(self, request) -> web.Response:
        """GET /api/prompt_manager/search - Search prompts."""
        try:
            query = request.query.get('q', '')
            limit = int(request.query.get('limit', 50))
            
            results = self.db_manager.search_prompts(query, limit)
            
            return web.json_response({
                'success': True,
                'results': results,
                'query': query
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)
    
    async def find_duplicates(self, request) -> web.Response:
        """GET /api/prompt_manager/duplicates - Find duplicate prompts."""
        try:
            duplicates = self.db_manager.find_duplicate_prompts()
            
            return web.json_response({
                'success': True,
                'duplicates': duplicates,
                'count': len(duplicates)
            })
        except Exception as e:
            return web.json_response({
                'success': False,
                'error': str(e)
            }, status=500)