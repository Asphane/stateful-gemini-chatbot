# ==============================================================================
# SECTION 1: IMPORTS & ENVIRONMENT CONFIGURATION
# ==============================================================================
import os
import sqlite3
from typing import TypedDict, Annotated
from dotenv import load_dotenv
import requests

# LangChain and LangGraph core components
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool

# Load environment variables (e.g., GOOGLE_API_KEY) from .env file
load_dotenv()

search_tool = DuckDuckGoSearchRun(region='us-en')


# ==============================================================================
# SECTION 2: MODEL INITIALIZATION & DATABASE CONNECTION
# ==============================================================================
# Initialize Google Gemini Generative AI Chat Model
llm = ChatGoogleGenerativeAI(model='gemini-2.5-flash')

# Establish SQLite database connection for persistent checkpointer state
# `check_same_thread=False` allows SQLite access across Streamlit worker threads
conn = sqlite3.connect(database='chatbot.db', check_same_thread=False)

# Checkpointer to automatically persist state snapshots across turns and threads
checkpointer = SqliteSaver(conn=conn)

SYSTEM_PROMPT = """
You are a helpful AI assistant.

Rules:

1. Use calculator for mathematical operations.
2. Use get_stock_price for stock queries.
3. Use search_web for current events and recent information.
4. Be concise and factual.
5. Never hallucinate stock prices.
"""


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
# SECTION 4: TOOLS
# ==============================================================================

@tool
def calculator(first_num: float, second_num: float, operation: str) -> dict:
    """
    Perform a basic arithmetic operation on two numbers.
    Supported operations: add, sub, mul, div
    """
    try:
        if operation == "add":
            result = first_num + second_num
        elif operation == "sub":
            result = first_num - second_num
        elif operation == "mul":
            result = first_num * second_num
        elif operation == "div":
            if second_num == 0:
                return {"error": "Division by zero is not allowed"}
            result = first_num / second_num
        else:
            return {"error": f"Unsupported operation '{operation}'"}
        
        return {"first_num": first_num, "second_num": second_num, "operation": operation, "result": result}
    except Exception as e:
        return {"error": str(e)}

@tool
def get_stock_price(symbol: str) -> dict:
    """
    Get real-time stock price.

    Use this tool whenever the user asks:
    - stock price
    - share price
    - market price
    - trading price

    Examples:
    NVIDIA -> NVDA
    Apple -> AAPL
    Tesla -> TSLA
    """
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey=C9PE94QUEW9VWGFM"

    try:
        r = requests.get(
            url,
            timeout=10
        )
        r.raise_for_status()

        return r.json()

    except Exception as e:
        return {
            "error": str(e)
        }

@tool
def search_web(query: str) -> str:
    """Search the web for current information relevant to the user's query."""
    print(f"SEARCH TOOL CALLED: {query}")

    result = search_tool.invoke(query)
    try:
        return str(result)

    except Exception as e:

        return f"Search failed: {e}"


tools = [calculator, get_stock_price, search_web]
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

# ==============================================================================
# SECTION 5: NODE DEFINITIONS (BUSINESS LOGIC)
# ==============================================================================
def chat_node(state: ChatState) -> ChatState:
    """
    Main processing node that handles chat conversation.
    
    1. Reads accumulated conversation messages from state.
    2. Invokes LLM with the message list.
    3. Returns the LLM response message to be appended to state via `add_messages`.
    """
    msgs = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    res = llm_with_tools.invoke(msgs)
    return {'messages': [res]}


# ==============================================================================
# SECTION 5: STATE GRAPH CONSTRUCTION & COMPILATION
# ==============================================================================
# Initialize StateGraph with defined ChatState schema
graph = StateGraph(ChatState)

# Add nodes to graph
graph.add_node("chat", chat_node)
graph.add_node("tools", tool_node)

# Route tool calls back through the model; finish when the model returns a
# regular response.
graph.add_edge(START, "chat")
graph.add_conditional_edges("chat", tools_condition)
graph.add_edge("tools", "chat")

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

if __name__ == "__main__":
    config = {
        "configurable": {
            "thread_id": "test_thread"
        }
    }

    response = chatbot.invoke(
        {
            "messages": [
                HumanMessage(
                    content="What is 25 multiplied by 4?"
                )
            ]
        },
        config=config
    )

    res = response["messages"][-1].content
    ans=""
    if isinstance(res, list):
        res = "".join(
            item.get("text", "")
            for item in res
            if isinstance(item, dict)
        )
    else:
        res = str(res)

    print(res)