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
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool, BaseTool, StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient
import aiosqlite
import asyncio
import threading

# Load environment variables (e.g., GOOGLE_API_KEY) from .env file
load_dotenv()

search_tool = DuckDuckGoSearchRun(region='us-en')

# Dedicated async loop for backend tasks
_ASYNC_LOOP = asyncio.new_event_loop()
_ASYNC_THREAD = threading.Thread(target=_ASYNC_LOOP.run_forever, daemon=True)
_ASYNC_THREAD.start()

def _submit_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _ASYNC_LOOP)


def run_async(coro):
    return _submit_async(coro).result()


def submit_async_task(coro):
    """Schedule a coroutine on the backend event loop."""
    return _submit_async(coro)

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
You are a versatile, intelligent AI assistant equipped with a rich set of tools.

Available capabilities:
1. Mathematical operations via the `calculator` tool.
2. Real-time stock market data via `get_stock_price`.
3. General web search via `search_web`.
4. Browser automation via Playwright MCP tools (`browser_navigate`, `browser_snapshot`, `browser_click`, etc.) for visiting web pages and extracting live content from URLs.
5. Personal finance & expense management via Expense MCP tools (`add_expense`, `list_expenses`, `summarize`).

Guidelines:
- When a user asks what you can do, summarize all your capabilities (math, stocks, web search, web browser navigation, and expense management).
- When given a specific URL or webpage to inspect, use browser automation (`browser_navigate`) or search tools to extract and inspect the page content.
- Be concise, clear, and factual. Never hallucinate real-time data or stock prices.
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
# SECTION 4: TOOLS & MCP SERVERS
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
    
    try:
        result = search_tool.invoke(query)
        return str(result)

    except Exception as e:
        return f"Search failed: {e}"


# Multi-Server MCP Client configuration
MCP_SERVERS = {
    "expense": {
        "transport": "streamable_http",
        "url": "https://splendid-gold-dingo.fastmcp.app/mcp"
    },
    "playwright": {
        "transport": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "@playwright/mcp@latest"
        ]
    }
}

def _make_sync_tool(t: BaseTool) -> StructuredTool:
    """Wraps an async MCP tool with a synchronous runner for LangGraph ToolNode compatibility."""
    def sync_run(*args, **kwargs):
        input_data = kwargs if kwargs else (args[0] if args else {})
        return run_async(t.ainvoke(input_data))

    return StructuredTool(
        name=t.name,
        description=t.description,
        args_schema=t.args_schema,
        func=sync_run,
        coroutine=t.coroutine,
    )

def load_mcp_tools() -> list[BaseTool]:
    all_mcp_tools = []
    for server_name, srv_config in MCP_SERVERS.items():
        try:
            client = MultiServerMCPClient({server_name: srv_config})
            srv_tools = run_async(client.get_tools())
            sync_tools = [_make_sync_tool(t) for t in srv_tools]
            all_mcp_tools.extend(sync_tools)

            print(f"Loaded {len(sync_tools)} tools from MCP server '{server_name}'")

        except Exception as e:
            print(f"MCP Error on '{server_name}': {e}")
    return all_mcp_tools


mcp_tools = load_mcp_tools()
tools = [calculator, get_stock_price, search_web, *mcp_tools]
llm_with_tools = llm.bind_tools(tools) if tools else llm
tool_node = ToolNode(tools) if tools else None


# ==============================================================================
# SECTION 5: NODE DEFINITIONS & STATE GRAPH CONSTRUCTION
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


# Initialize StateGraph with defined ChatState schema
graph = StateGraph(ChatState)

# Add nodes to graph
graph.add_node("chat", chat_node)
graph.add_edge(START, "chat")

# Route tool calls back through the model; finish when the model returns a regular response
if tool_node:
    graph.add_node("tools", tool_node)
    graph.add_conditional_edges("chat", tools_condition, {
        "tools": "tools",
        "__end__": END
    })
    graph.add_edge("tools", "chat")
else:
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