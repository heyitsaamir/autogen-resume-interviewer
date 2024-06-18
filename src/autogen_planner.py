from botbuilder.schema import Attachment
from botbuilder.core import CardFactory, TurnContext
from teams.ai.prompts import Message
from teams.ai.planners import Planner, Plan, PredictedSayCommand
from autogen import Agent, ConversableAgent, GroupChat, GroupChatManager, ChatResult
from autogen.agentchat.agent import Agent
import nest_asyncio
from state import AppTurnState
from typing import List, Callable
from teams_user_proxy import TeamsUserProxy
from datetime import datetime
nest_asyncio.apply()


class AutoGenPlanner(Planner):
    def __init__(self, llm_config, build_group_chat: Callable[[TurnContext, AppTurnState, List[Agent]], GroupChat]) -> None:
        self.llm_config = llm_config
        self.build_group_chat = build_group_chat
        super().__init__()

    async def begin_task(self, context, state: AppTurnState):
        return await self.continue_task(context, state)

    async def continue_task(self, context, state: AppTurnState):
        user_proxy = TeamsUserProxy(
            name="User",
            system_message="A human admin. This agent is a proxy for the user. This agent can help answer questions too.",
            llm_config=self.llm_config
        )

        groupchat = self.build_group_chat(context, state, [user_proxy])
        if state.conversation.is_waiting_for_user_input and state.conversation.started_waiting_for_user_input_at is not None:
            # if the user has not responded in 2 minutes
            if (datetime.now() - state.conversation.started_waiting_for_user_input_at).total_seconds() > 2 * 60:
                state.conversation.is_waiting_for_user_input = False
                state.conversation.started_waiting_for_user_input_at = None

        is_existing_group_chat = state.conversation.is_waiting_for_user_input and state.conversation.message_history is not None
        manager = GroupChatManager(
            groupchat=groupchat,
            llm_config=self.llm_config
        )
        if is_existing_group_chat and state.conversation.message_history is not None:
            await manager.a_resume(messages=state.conversation.message_history)

        chat_result = await user_proxy.a_initiate_chat(recipient=manager, message=context.activity.text, clear_history=False, summary_method="reflection_with_llm")
        chat_history = chat_result.chat_history[:]
        for chat in chat_history:
            if chat.get("content") == "":
                chat_result.chat_history.remove(chat)

        if user_proxy.question_for_user is not None:
            state.conversation.is_waiting_for_user_input = True
            state.conversation.started_waiting_for_user_input_at = datetime.now()
            message = user_proxy.question_for_user
        else:
            state.conversation.is_waiting_for_user_input = False
            state.conversation.started_waiting_for_user_input_at = None
            message = chat_result.summary

        state.conversation.message_history = chat_result.chat_history
        return Plan(
            commands=[
                PredictedSayCommand(
                    'SAY', 
                    Message(
                        'assistant', 
                        content=message, 
                        # attachments=[create_chat_history_ac(chat_result)]
                        )
                    )
                ]
            )


def create_chat_history_ac(message: ChatResult) -> Attachment:
    facts = []
    for value in message.chat_history:
        if value.get("name") is not None:
            facts.append({
                "title": value["name"],
                "value": value["content"]
            })

    return CardFactory.adaptive_card(
        {
            "type": "AdaptiveCard",
            "speak": "3 minute energy flow with kayo video",
            "$schema": "https://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.5",
            "body": [
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": "Agent Reasoning",
                    "style": "heading",
                    "size": "Medium"
                },
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "text": "You can view the agent's internal conversation by toggling the card below"
                },
                {
                    "type": "ActionSet",
                    "actions": [
                        {
                            "type": "Action.ShowCard",
                            "title": "Show internal discussion",
                            "card": {
                                "type": "AdaptiveCard",
                                "body": [
                                    {
                                        "type": "FactSet",
                                        "facts": facts
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )
