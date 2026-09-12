# ==============================================================================
# SECTION 1: IMPORTS & APPLICATION CONFIGURATION
# ==============================================================================
import os
import uuid
import streamlit as st
from langchain_core.messages import BaseMessage, HumanMessage
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
    st.session_state['thread_id'] = str(generate_thread_id())

# 3. chat_thread: List of all thread IDs created in the current user session
if 'chat_thread' not in st.session_state:
    st.session_state['chat_thread'] = []

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

st.sidebar.header('My Conversations')

# Render existing chat threads in reverse chronological order (newest first)
for thread_id in st.session_state['chat_thread'][::-1]:
    # Use saved thread title or fallback to truncated thread ID
    title = st.session_state['thread_titles'].get(
        thread_id,
        f"Chat-{thread_id[:8]}"
    )
    
    # Clicking a thread button switches the active conversation
    if st.sidebar.button(title, key=f"btn_{thread_id}", use_container_width=True):
        st.session_state['thread_id'] = thread_id
        
        # Retrieve message history for the selected thread from LangGraph checkpoint
        messages = load_conversation(thread_id)
        
        # Convert LangChain message objects into UI-friendly format
        tmp_msg = []
        for msg in messages:
            role = "user" if isinstance(msg, HumanMessage) else "assistant"
            content = extract_content(msg)
            tmp_msg.append({
                "role": role,
                "content": content
            })
        
        st.session_state['message_history'] = tmp_msg
        st.rerun()


# ==============================================================================
# SECTION 5: MAIN CHAT DISPLAY AREA
# ==============================================================================
# Render all previously exchanged messages in the current conversation
for message in st.session_state['message_history']:
    with st.chat_message(message['role']):
        st.text(message['content'])


# ==============================================================================
# SECTION 6: USER INPUT HANDLING & STREAMING RESPONSE
# ==============================================================================
# Render the bottom chat input bar
user_input = st.chat_input('Type Here.....')

if user_input:
    # 1. Record and display the user's message immediately in the UI
    st.session_state['message_history'].append({
        'role': 'user',
        'content': user_input
    })

    with st.chat_message('user'):
        st.text(user_input)

    # 2. Prepare LangGraph configuration with the active thread_id for state persistence
    config = {
        'configurable': {
            'thread_id': st.session_state['thread_id']
        },
        'metadata': {
            'thread_id': st.session_state['thread_id']
        },
        'run_name': 'chat_turn'
    }

    # 3. Stream the LLM response chunk-by-chunk in real-time
    with st.chat_message('assistant'):
        placeholder = st.empty()
        ai_msg = ""
        current_thread = st.session_state["thread_id"]

        # Set a meaningful sidebar title from the first 30 characters of the initial prompt
        if current_thread not in st.session_state['thread_titles']:
            title = user_input[:30]
            st.session_state['thread_titles'][current_thread] = title

        # Stream tokens from LangGraph chatbot node
        for message_chunk, metadata in chatbot.stream(
            {'messages': [HumanMessage(content=user_input)]},
            config=config,
            stream_mode='messages'
        ):
            content = message_chunk.content

            # Handle list of structured content blocks (multimodal / tools / text)
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        txt = item.get("text", "")
                        if txt:
                            ai_msg += txt
                            placeholder.text(ai_msg)

            # Handle plain string content tokens
            elif isinstance(content, str):
                ai_msg += content
                placeholder.text(ai_msg)

    # 4. Append complete assistant response to message history for UI persistence
    st.session_state['message_history'].append({
        'role': 'assistant',
        'content': ai_msg
    })