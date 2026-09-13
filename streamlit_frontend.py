# ==============================================================================
# SECTION 1: IMPORTS & APPLICATION CONFIGURATION
# ==============================================================================
import os
import uuid
import streamlit as st
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage, AIMessage
from langgraph_backend import chatbot, retrieve_all_threads

# Set environment variable for process/app identification
os.environ['PERSONAL CHATBOT'] = 'streamlit_frontend.py'

# Configure Streamlit page settings (Title, Favicon Icon, Layout Mode)
st.set_page_config(
    page_title="LangGraph LLM Chatbot",
    page_icon="🤖",
    layout="wide"
)

# Display main application header
st.title("🤖 LangGraph LLM ChatBot")


# ==============================================================================
# SECTION 2: UTILITY & HELPER FUNCTIONS
# ==============================================================================

def generate_thread_id() -> str:
    """
    Generates a unique identifier (UUID4) for chat threads.
    Each conversation thread in LangGraph is tracked by a distinct thread_id.
    """
    return str(uuid.uuid4())


def reset_chat() -> None:
    """
    Resets the active chat session:
    1. Generates a brand new thread ID.
    2. Updates the session state's active thread_id.
    3. Clears the current UI message history.
    4. Registers the new thread in the thread tracking list.
    """
    thread_id = generate_thread_id()
    st.session_state['thread_id'] = thread_id
    st.session_state['message_history'] = []
    add_thread(thread_id)


def add_thread(thread_id: str) -> None:
    """
    Appends a thread_id to the session state list of known chat threads
    if it is not already present.
    """
    if thread_id not in st.session_state['chat_thread']:
        st.session_state['chat_thread'].append(thread_id)

def delete_thread(thread_id: str) -> None:
    """
    Delete a conversation from the UI.
    Does NOT delete data from SQLite.
    """

    if thread_id in st.session_state["chat_thread"]:
        st.session_state["chat_thread"].remove(thread_id)

    if thread_id in st.session_state["thread_titles"]:
        del st.session_state["thread_titles"][thread_id]

    # If the currently opened chat is deleted
    if st.session_state["thread_id"] == thread_id:

        new_thread = generate_thread_id()

        st.session_state["thread_id"] = new_thread
        st.session_state["message_history"] = []

        add_thread(new_thread)


def load_conversation(thread_id: str) -> list:
    """
    Fetches the conversation state and stored messages from the LangGraph
    backend's SQLite checkpointer for a given thread_id.
    
    Returns:
        list: List of BaseMessage objects (HumanMessage, AIMessage) from the graph state.
    """
    state = chatbot.get_state(
        config={
            'configurable': {
                'thread_id': thread_id
            }
        }
    )
    # Check if 'messages' key exists in state values, return an empty list if not found
    return state.values.get('messages', [])


def extract_content(msg: BaseMessage) -> str:
    """
    Extracts text content safely from a message object.
    Handles both plain string content and structured multimodal/chunked list formats.
    """
    if isinstance(msg.content, list):
        text = ""
        for item in msg.content:
            if isinstance(item, dict):
                text += item.get("text", "")
        return text
    return str(msg.content)


# ==============================================================================
# SECTION 3: SESSION STATE INITIALIZATION
# ==============================================================================
# Streamlit reruns the whole script on every user interaction.
# We initialize session state variables to persist data across reruns.

# 1. message_history: List of dicts [{'role': 'user'|'assistant', 'content': '...'}] for current UI rendering
if 'message_history' not in st.session_state:
    st.session_state['message_history'] = []

# 2. thread_id: Active conversation thread identifier
if 'thread_id' not in st.session_state:
    st.session_state['thread_id'] = generate_thread_id()

# 3. chat_thread: List of all thread IDs created in the current user session
if 'chat_thread' not in st.session_state:
    stored_threads = retrieve_all_threads()
    st.session_state['chat_thread'] = stored_threads if stored_threads else [st.session_state['thread_id']]

# 4. thread_titles: Mapping of thread_id -> custom display title (first prompt snippet)
if 'thread_titles' not in st.session_state:
    st.session_state['thread_titles'] = {}

# Ensure the current active thread is tracked in the list
add_thread(st.session_state['thread_id'])


# ==============================================================================
# SECTION 4: SIDEBAR COMPONENT (CONVERSATION HISTORY & MANAGEMENT)
# ==============================================================================
st.sidebar.title('LangGraph Chatbot')

# Button to start a new chat conversation
if st.sidebar.button('➕ New Chat', use_container_width=True):
    reset_chat()
    st.rerun()

st.sidebar.header('My Conversations')

# Render existing chat threads in reverse chronological order (newest first)
for thread_id in st.session_state["chat_thread"][::-1]:

    title = st.session_state["thread_titles"].get(
        thread_id,
        f"Chat-{thread_id[:8]}"
    )

    col1, col2 = st.sidebar.columns([4, 2])

    # ==========================
    # OPEN CHAT BUTTON
    # ==========================
    with col1:

        if st.button(
            title,
            key=f"open_{thread_id}",
            use_container_width=True
        ):

            st.session_state["thread_id"] = thread_id

            messages = load_conversation(thread_id)

            tmp_msg = []

            for msg in messages:

                if isinstance(msg, HumanMessage):
                    role = "user"

                elif isinstance(msg, ToolMessage):
                    continue

                else:
                    role = "assistant"

                tmp_msg.append(
                    {
                        "role": role,
                        "content": extract_content(msg)
                    }
                )

            st.session_state["message_history"] = tmp_msg

            st.rerun()

    # ==========================
    # DELETE BUTTON
    # ==========================
    with col2:

        if st.button(
            "❌",
            key=f"delete_{thread_id}",
            use_container_width=True
        ):

            delete_thread(thread_id)

            st.rerun()


# ==============================================================================
# SECTION 5: MAIN CHAT DISPLAY AREA
# ==============================================================================
# Render all previously exchanged messages in the current conversation
for message in st.session_state['message_history']:
    with st.chat_message(message['role']):
        st.markdown(message['content'])


# ==============================================================================
# SECTION 6: USER INPUT HANDLING & STREAMING RESPONSE
# ==============================================================================
user_input = st.chat_input("Type Here.....")

if user_input:

    # Auto-generate title for current thread from first message
    if st.session_state["thread_id"] not in st.session_state["thread_titles"]:
        st.session_state["thread_titles"][st.session_state["thread_id"]] = (
            user_input[:25] + ("..." if len(user_input) > 25 else "")
        )

    st.session_state["message_history"].append(
        {
            "role": "user",
            "content": user_input
        }
    )

    with st.chat_message("user"):
        st.markdown(user_input)

    config = {
        "configurable": {
            "thread_id": st.session_state["thread_id"]
        },
        "metadata": {
            "thread_id": st.session_state["thread_id"]
        },
        "run_name": "chat_turn"
    }

    with st.chat_message("assistant"):

        response_placeholder = st.empty()
        full_response = ""
        status_box = None
        used_tools = []

        tool_labels = {
            "search_web": "🌐 Searching Web",
            "calculator": "🧮 Calculator",
            "get_stock_price": "📈 Stock Price Lookup",
            "browser_navigate": "🌐 Navigating Browser",
            "browser_snapshot": "📸 Taking Page Snapshot",
            "browser_click": "🖱️ Clicking Element",
            "browser_take_screenshot": "📷 Capturing Screenshot",
            "add_expense": "💰 Adding Expense",
            "list_expenses": "📊 Listing Expenses",
            "summarize": "📋 Summarizing Expenses"
        }

        # ---------------------------------------------------------
        # STREAM TOKENS AND TOOL EVENTS
        # ---------------------------------------------------------
        for message_chunk, metadata in chatbot.stream(
            {
                "messages": [
                    HumanMessage(content=user_input)
                ]
            },
            config=config,
            stream_mode="messages"
        ):

            # 1. Tool execution events
            if isinstance(message_chunk, ToolMessage):
                tool_name = getattr(
                    message_chunk,
                    "name",
                    "unknown_tool"
                )

                if tool_name not in used_tools:
                    used_tools.append(tool_name)

                display_name = tool_labels.get(
                    tool_name,
                    tool_name
                )

                if status_box is None:
                    status_box = st.status(
                        f"🔧 {display_name}",
                        expanded=False
                    )
                else:
                    status_box.update(
                        label=f"🔧 {display_name}",
                        state="running",
                        expanded=False
                    )

            # 2. AI response token streaming
            elif isinstance(message_chunk, AIMessage):
                content = extract_content(message_chunk)
                if content:
                    full_response += content
                    response_placeholder.markdown(full_response)

        # 3. Final tool status update
        if status_box:
            readable_tools = [
                tool_labels.get(t, t)
                for t in used_tools
            ]
            status_box.update(
                label=f"✅ Used: {', '.join(readable_tools)}",
                state="complete",
                expanded=False
            )

        ai_message = full_response

    st.session_state["message_history"].append(
        {
            "role": "assistant",
            "content": ai_message
        }
    )