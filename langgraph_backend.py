# ==============================================================================
# SECTION 1: IMPORTS & ENVIRONMENT CONFIGURATION
# ==============================================================================
import os
import sqlite3
from typing import TypedDict, Annotated
from dotenv import load_dotenv

# LangChain and LangGraph core components
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver

# Load environment variables (e.g., GOOGLE_API_KEY) from .env file
load_dotenv()


# ==============================================================================
# SECTION 2: MODEL INITIALIZATION & DATABASE CONNECTION
# ==============================================================================
# Initialize Google Gemini Generative AI Chat Model
llm = ChatGoogleGenerativeAI(model='gemini-3.6-flash')

# Establish SQLite database connection for persistent checkpointer state
# `check_same_thread=False` allows SQLite access across Streamlit worker threads
conn = sqlite3.connect(database='chatbot.db', check_same_thread=False)

# Checkpointer to automatically persist state snapshots across turns and threads
checkpointer = SqliteSaver(conn=conn)


# ==============================================================================
# SECTION 3: GRAPH STATE DEFINITION
# ==============================================================================
class ChatState(TypedDict):
    """
    Represents the schema of the state passed between graph nodes.
    
    Attributes:
        messages: Annotated with `add_messages` reducer to automatically append
                  new messages to the conversation history instead of overwriting.
    """
    messages: Annotated[list[BaseMessage], add_messages]


# ==============================================================================
# SECTION 4: NODE DEFINITIONS (BUSINESS LOGIC)
# ==============================================================================
def chat_node(state: ChatState) -> ChatState:
    """
    Main processing node that handles chat conversation.
    
    1. Reads accumulated conversation messages from state.
    2. Invokes LLM with the message list.
    3. Returns the LLM response message to be appended to state via `add_messages`.
    """
    msgs = state['messages']
    res = llm.invoke(msgs)
    return {'messages': [res]}


# ==============================================================================
# SECTION 5: STATE GRAPH CONSTRUCTION & COMPILATION
# ==============================================================================
# Initialize StateGraph with defined ChatState schema
graph = StateGraph(ChatState)

# Add nodes to graph
graph.add_node("chat", chat_node)

# Define control flow edges (START -> chat node -> END)
graph.add_edge(START, "chat")
graph.add_edge("chat", END)

# Compile graph with SQLite checkpointing enabled for thread persistence
chatbot = graph.compile(checkpointer=checkpointer)


# ==============================================================================
# SECTION 6: THREAD MANAGEMENT UTILITIES
# ==============================================================================
def retrieve_all_threads() -> list[str]:
    """
    Scans all stored checkpoints in the SQLite checkpointer
    and returns a unique list of all historical thread IDs.
    """
    all_threads = set()

    for checkpoint in checkpointer.list(None):
        thread_id = checkpoint.config.get('configurable', {}).get('thread_id')
        if thread_id:
            all_threads.add(thread_id)

    return list(all_threads)


# ==============================================================================
# SECTION 7: TESTING & DEBUG USAGE EXAMPLES (OPTIONAL)
# ==============================================================================
# if __name__ == "__main__":
#     # Example 1: Invoke chatbot with a test thread ID
#     config = {'configurable': {'thread_id': 'test_thread'}}
#     res = chatbot.invoke(
#         {'messages': [HumanMessage(content="Hello, how are you?")]},
#         config=config
#     )
#     print("Response:", res['messages'][-1].content)
#
#     # Example 2: List all active thread IDs from database
#     print("Stored Threads:", retrieve_all_threads())