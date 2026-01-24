-- MBTA Agntcy Observability Schema
-- Owner: mani

CREATE DATABASE IF NOT EXISTS mbta_logs;
USE mbta_logs;

-- Conversations table
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id String,
    user_id String,
    timestamp DateTime,
    message_role String,
    message_content String,
    intent String,
    routed_to_orchestrator UInt8,
    metadata String
) ENGINE = MergeTree()
ORDER BY (conversation_id, timestamp)
PARTITION BY toYYYYMM(timestamp);

-- Agent invocations table
CREATE TABLE IF NOT EXISTS agent_invocations (
    invocation_id String,
    conversation_id String,
    agent_name String,
    timestamp DateTime,
    duration_ms Float32,
    status String,
    error_message String,
    request_payload String,
    response_payload String
) ENGINE = MergeTree()
ORDER BY (timestamp, agent_name)
PARTITION BY toYYYYMM(timestamp);

-- LLM calls table
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id String,
    conversation_id String,
    timestamp DateTime,
    model String,
    prompt_tokens Int32,
    completion_tokens Int32,
    total_tokens Int32,
    duration_ms Float32,
    intent String,
    confidence Float32
) ENGINE = MergeTree()
ORDER BY timestamp
PARTITION BY toYYYYMM(timestamp);

-- Agent performance summary (FIXED)
CREATE MATERIALIZED VIEW IF NOT EXISTS agent_performance_summary
ENGINE = SummingMergeTree()
ORDER BY (agent_name, hour)
AS SELECT
    agent_name,
    toStartOfHour(timestamp) as hour,
    count() as total_calls,
    sum(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_calls,
    avg(duration_ms) as avg_duration_ms
FROM agent_invocations
GROUP BY agent_name, hour;

-- Conversation summary
CREATE MATERIALIZED VIEW IF NOT EXISTS conversation_summary
ENGINE = SummingMergeTree()
ORDER BY (hour, intent)
AS SELECT
    toStartOfHour(timestamp) as hour,
    intent,
    count(DISTINCT conversation_id) as unique_conversations,
    count() as total_messages,
    sum(routed_to_orchestrator) as orchestrated_messages
FROM conversations
GROUP BY hour, intent;