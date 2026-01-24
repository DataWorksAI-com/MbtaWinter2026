from clickhouse_driver import Client
from typing import Dict, Any, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class ClickHouseClient:
    """ClickHouse client for logging and analytics"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config['database']['clickhouse']
        self.client = Client(
            host=self.config['host'],
            port=self.config['port'],
            database=self.config['database'],
            user=self.config['user'],
            password=self.config['password']
        )
        
        self._create_tables()
        
        logger.info("ClickHouseClient initialized")
    
    def _create_tables(self):
        """Create necessary tables"""
        
        # Conversations table
        self.client.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id String,
                user_id String,
                timestamp DateTime,
                message_role String,
                message_content String,
                metadata String
            ) ENGINE = MergeTree()
            ORDER BY (conversation_id, timestamp)
        """)
        
        # Agent invocations table
        self.client.execute("""
            CREATE TABLE IF NOT EXISTS agent_invocations (
                invocation_id String,
                conversation_id String,
                agent_name String,
                timestamp DateTime,
                duration_ms Float32,
                status String,
                error_message String
            ) ENGINE = MergeTree()
            ORDER BY (timestamp, agent_name)
        """)
        
        # Events log table
        self.client.execute("""
            CREATE TABLE IF NOT EXISTS event_logs (
                event_id String,
                event_type String,
                timestamp DateTime,
                service_name String,
                data String
            ) ENGINE = MergeTree()
            ORDER BY (timestamp, event_type)
        """)
        
        logger.info("ClickHouse tables created/verified")
    
    def log_conversation(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata: Dict[str, Any] = None
    ):
        """Log a conversation message"""
        import json
        
        self.client.execute(
            """
            INSERT INTO conversations 
            (conversation_id, user_id, timestamp, message_role, message_content, metadata)
            VALUES
            """,
            [(
                conversation_id,
                user_id,
                datetime.now(),
                role,
                content,
                json.dumps(metadata or {})
            )]
        )
    
    def log_agent_invocation(
        self,
        invocation_id: str,
        conversation_id: str,
        agent_name: str,
        duration_ms: float,
        status: str,
        error_message: str = ""
    ):
        """Log an agent invocation"""
        
        self.client.execute(
            """
            INSERT INTO agent_invocations
            (invocation_id, conversation_id, agent_name, timestamp, duration_ms, status, error_message)
            VALUES
            """,
            [(
                invocation_id,
                conversation_id,
                agent_name,
                datetime.now(),
                duration_ms,
                status,
                error_message
            )]
        )
    
    def log_event(
        self,
        event_id: str,
        event_type: str,
        service_name: str,
        data: Dict[str, Any]
    ):
        """Log a general event"""
        import json
        
        self.client.execute(
            """
            INSERT INTO event_logs
            (event_id, event_type, timestamp, service_name, data)
            VALUES
            """,
            [(
                event_id,
                event_type,
                datetime.now(),
                service_name,
                json.dumps(data)
            )]
        )
    
    def get_conversation_history(
        self,
        conversation_id: str
    ) -> List[Dict[str, Any]]:
        """Get conversation history"""
        
        result = self.client.execute(
            """
            SELECT timestamp, message_role, message_content, metadata
            FROM conversations
            WHERE conversation_id = %(conversation_id)s
            ORDER BY timestamp ASC
            """,
            {'conversation_id': conversation_id}
        )
        
        import json
        return [
            {
                'timestamp': row[0],
                'role': row[1],
                'content': row[2],
                'metadata': json.loads(row[3])
            }
            for row in result
        ]
    
    def get_agent_stats(
        self,
        agent_name: str = None,
        start_time: datetime = None,
        end_time: datetime = None
    ) -> Dict[str, Any]:
        """Get agent performance statistics"""
        
        where_clauses = []
        params = {}
        
        if agent_name:
            where_clauses.append("agent_name = %(agent_name)s")
            params['agent_name'] = agent_name
        
        if start_time:
            where_clauses.append("timestamp >= %(start_time)s")
            params['start_time'] = start_time
        
        if end_time:
            where_clauses.append("timestamp <= %(end_time)s")
            params['end_time'] = end_time
        
        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        result = self.client.execute(
            f"""
            SELECT 
                agent_name,
                COUNT(*) as total_invocations,
                AVG(duration_ms) as avg_duration,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed
            FROM agent_invocations
            WHERE {where_clause}
            GROUP BY agent_name
            """,
            params
        )
        
        return {
            row[0]: {
                'total_invocations': row[1],
                'avg_duration_ms': row[2],
                'successful': row[3],
                'failed': row[4],
                'success_rate': row[3] / row[1] if row[1] > 0 else 0
            }
            for row in result
        }