"""AI Agent service using Google Gemini."""

import google.generativeai as genai
from typing import Optional, Union

from app.config import get_settings
from app.models.email import EmailMessage
from app.services.search_service import SearchService
from app.services.memory_service import MemoryService
from app.services.vector_service import VectorService
from app.services.notion_service import NotionService
from app.utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are Moe's highly capable personal AI assistant responding via email. You are thoughtful, accurate, and concise.

## Your Identity
- Name: AI Assistant
- Purpose: Help Moe with questions, research, and tasks via email
- Personality: Professional, friendly, efficient

## Your Capabilities
1. Answer questions using extensive knowledge
2. Search the web for current information (news, weather, prices, sports, events)
3. Remember past conversations with this user
4. Search past email conversations for relevant context
5. Analyze images and screenshots sent via email
6. Create tasks and notes in Notion to keep Moe organized

## Response Guidelines
- START with the direct answer - no preamble like "Great question!" or "Sure, I'd be happy to help"
- Be ACCURATE - if unsure, search the web or say you don't know
- Be CONCISE - aim for 2-4 paragraphs unless more detail is needed
- Be COMPLETE - anticipate and answer likely follow-up questions
- Be NATURAL - write like a helpful colleague, not a robot

## When to Use Tools
- Web Search: Current events, weather, prices, news, sports scores, stock prices, anything time-sensitive
- Past Conversations: When user references previous discussions or you need context
- Notion Tasks: Whenever Moe asks you to "remember this for later", "add this to my to-do list", or if there is a clear actionable item in the email that Moe needs to track. Use it proactively if you notice a clear task.

## Image Analysis
- When images are attached, analyze them thoroughly
- Describe what you see, extract text (OCR), identify objects, explain diagrams
- If the user asks a question about the image, focus your answer on that
- If no specific question, provide a helpful summary of the image content

## Formatting Rules (IMPORTANT)
- Write in plain text suitable for email
- For emphasis: Use CAPS sparingly or rephrase
- For lists: Use "1." "2." "3." or write in prose
- Keep paragraphs short (2-4 sentences)

## Before responding:
1. Identify what the user is really asking
2. Determine if you need to search the web, past conversations, or create a task
3. Gather all relevant information
4. Formulate a clear, complete answer
5. Review for accuracy and clarity
Do not show your thinking process to the user - just provide the final answer.

## Quality Checks
Before sending your response, verify:
- Did I actually answer their question?
- Is my information current? (Use web search if unsure)
- Is the response clear and easy to read?
- Have I avoided technical jargon?
- Is the length appropriate (not too long, not too short)?
"""


class AIService:
    """AI Agent service for processing email queries."""

    def __init__(
        self,
        search_service: SearchService,
        memory_service: MemoryService,
        vector_service: VectorService,
        notion_service: Optional[NotionService] = None,
    ):
        self.settings = get_settings()
        self.search_service = search_service
        self.memory_service = memory_service
        self.vector_service = vector_service
        self.notion_service = notion_service

        # Configure Gemini
        genai.configure(api_key=self.settings.gemini_api_key)

        # Define tools for function calling
        self.tools = [
            genai.protos.Tool(
                function_declarations=[
                    genai.protos.FunctionDeclaration(
                        name="web_search",
                        description="Search the web for current information. Use for real-time data like news, weather, prices, sports scores, or current events.",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "query": genai.protos.Schema(
                                    type=genai.protos.Type.STRING,
                                    description="The search query to look up",
                                )
                            },
                            required=["query"],
                        ),
                    ),
                    genai.protos.FunctionDeclaration(
                        name="search_past_conversations",
                        description="Search past email conversations for relevant context or previous discussions.",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "query": genai.protos.Schema(
                                    type=genai.protos.Type.STRING,
                                    description="What to search for in past conversations",
                                )
                            },
                            required=["query"],
                        ),
                    ),
                    genai.protos.FunctionDeclaration(
                        name="create_notion_task",
                        description="Create a task or actionable item in Moe's Notion database. Use this when asked to 'remember to do X' or 'add Y to my list', or when encountering action items.",
                        parameters=genai.protos.Schema(
                            type=genai.protos.Type.OBJECT,
                            properties={
                                "title": genai.protos.Schema(
                                    type=genai.protos.Type.STRING,
                                    description="The name or title of the task",
                                ),
                                "priority": genai.protos.Schema(
                                    type=genai.protos.Type.STRING,
                                    description="Priority of the task: 'High', 'Medium', or 'Low'",
                                ),
                                "status": genai.protos.Schema(
                                    type=genai.protos.Type.STRING,
                                    description="Status of the task: 'To Do', 'In Progress', or 'Done'",
                                )
                            },
                            required=["title"],
                        ),
                    ),
                ]
            )
        ]

        # Initialize model
        self.model = genai.GenerativeModel(
            model_name="models/gemini-2.5-flash",
            tools=self.tools,
            system_instruction=SYSTEM_PROMPT,
        )

        logger.info("ai_service_initialized")

    async def process_email(self, email_msg: EmailMessage) -> str:
        """
        Process an email and generate a response.

        Args:
            email_msg: The email message to process

        Returns:
            Generated response text
        """
        try:
            session_id = email_msg.sender_email

            # Get conversation history from PostgreSQL
            history = self.memory_service.get_conversation_history(session_id, limit=5)

            # Build text context
            text_context = (
                f"Current Email:\n"
                f"From: {email_msg.sender_email}\n"
                f"Subject: {email_msg.subject}\n\n"
                f"Message:\n{email_msg.body}"
            )

            # Build multimodal content (text + images)
            content_parts = [text_context]

            # Add image attachments if present
            if email_msg.attachments:
                content_parts.append(
                    f"\n\n[{len(email_msg.attachments)} image(s) attached - please analyze]"
                )
                for attachment in email_msg.attachments:
                    # Add image as inline data for Gemini
                    content_parts.append({
                        "mime_type": attachment.content_type,
                        "data": attachment.data,
                    })
                    logger.info(
                        "adding_image_to_context",
                        filename=attachment.filename,
                        content_type=attachment.content_type,
                    )

            # Create chat session with history, ensuring we don't exceed reasonable limits
            # Gemini 2.5 flash has a massive 1M token window, but for cost/latency, we limit the injected history text
            chat_history = []
            
            # Truncate history to roughly 30k characters max (~7-8k tokens) to prevent runaway context scaling
            max_history_chars = 30000 
            current_chars = 0
            
            # Add messages from newest to oldest up to limit, then reverse back
            recent_msgs = []
            for msg in reversed(history):
                msg_len = len(msg["content"])
                if current_chars + msg_len > max_history_chars and recent_msgs:
                    logger.info("history_truncated", total_messages=len(history), included=len(recent_msgs))
                    break
                recent_msgs.append(msg)
                current_chars += msg_len
                
            # Re-reverse to chronological order for Gemini
            for msg in reversed(recent_msgs):
                role = "user" if msg["role"] == "user" else "model"
                chat_history.append({"role": role, "parts": [msg["content"]]})

            chat = self.model.start_chat(history=chat_history)

            # Send message and handle function calls
            response = await self._send_with_tools(chat, content_parts, session_id)

            # Store conversation in memory (text only)
            user_message = email_msg.body
            if email_msg.attachments:
                user_message += f" [+{len(email_msg.attachments)} image(s)]"

            self.memory_service.add_conversation(
                session_id=session_id,
                user_message=user_message,
                assistant_message=response,
            )

            logger.info(
                "email_processed",
                sender=email_msg.sender_email,
                response_length=len(response),
                image_count=len(email_msg.attachments),
            )

            return response

        except Exception as e:
            logger.error("process_email_error", error=str(e))
            raise

    async def _send_with_tools(
        self,
        chat,
        message: Union[str, list],
        session_id: str,
        max_iterations: int = 5,
    ) -> str:
        """
        Send message and handle any function calls.

        Args:
            chat: The chat session
            message: User message (string or list of parts for multimodal)
            session_id: Session ID for context
            max_iterations: Maximum tool call iterations

        Returns:
            Final response text
        """
        response = chat.send_message(message)

        for _ in range(max_iterations):
            # Check for function calls
            if not response.candidates[0].content.parts:
                break

            function_calls = [
                part.function_call
                for part in response.candidates[0].content.parts
                if hasattr(part, "function_call") and part.function_call.name
            ]

            if not function_calls:
                break

            # Process function calls
            function_responses = []
            for fc in function_calls:
                result = await self._execute_function(fc.name, dict(fc.args), session_id)
                function_responses.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

            # Send function results back
            response = chat.send_message(function_responses)

        # Extract final text response
        text_parts = [
            part.text
            for part in response.candidates[0].content.parts
            if hasattr(part, "text") and part.text
        ]

        return "\n".join(text_parts) if text_parts else "I apologize, but I couldn't generate a response."

    async def _execute_function(
        self,
        function_name: str,
        args: dict,
        session_id: str,
    ) -> str:
        """Execute a function call and return result."""
        try:
            if function_name == "web_search":
                query = args.get("query", "")
                logger.info("executing_web_search", query=query)
                results = await self.search_service.search(query)
                return self.search_service.format_results(results)

            elif function_name == "search_past_conversations":
                query = args.get("query", "")
                logger.info("executing_rag_search", query=query)
                results = self.vector_service.search_similar(
                    query=query,
                    limit=5,
                    sender_filter=session_id,
                )
                return self.vector_service.format_search_results(results)

            elif function_name == "create_notion_task":
                title = args.get("title", "New Task")
                priority = args.get("priority", "Medium")
                status = args.get("status", "To Do")
                
                logger.info("executing_create_notion_task", title=title, priority=priority)
                
                if not self.notion_service or not self.notion_service.is_enabled():
                    return "Notion integration is not configured or enabled."
                    
                # We can try to extract the original email subject using session history context, but we will keep it simple for now
                success = await self.notion_service.create_task(
                    title=title,
                    priority=priority,
                    status=status,
                    source_email_subject=f"Email thread with {session_id}"
                )
                
                if success:
                    return f"Successfully created task '{title}' in Notion with priority '{priority}'."
                else:
                    return "Failed to create task in Notion."

            else:
                logger.warning("unknown_function", name=function_name)
                return f"Unknown function: {function_name}"

        except Exception as e:
            logger.error("function_execution_error", function=function_name, error=str(e))
            return f"Error executing {function_name}: {str(e)}"
