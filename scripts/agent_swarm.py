"""
Agent Swarm Tool Calling for DeepSeek Proxy.

Implements tool calling without requiring the official API.
Analyzes user messages and executes tools locally.
"""
import re
import os
import json
import glob as glob_module
import subprocess
from typing import Optional, Dict, Any, List, Tuple


class ToolExecutor:
    """Executes tools locally."""
    
    def read_file(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        """Read a file or directory."""
        try:
            if os.path.isdir(path):
                entries = []
                for item in sorted(os.listdir(path)):
                    full = os.path.join(path, item)
                    if os.path.isdir(full):
                        entries.append(item + "/")
                    else:
                        entries.append(item)
                return "\n".join(entries)
            
            with open(path, 'r', errors='replace') as f:
                lines = f.readlines()
            
            # Apply offset and limit
            start = max(0, offset - 1)  # Convert to 0-indexed
            end = start + limit if limit > 0 else len(lines)
            selected = lines[start:end]
            
            # Format with line numbers
            result = []
            for i, line in enumerate(selected, start=start + 1):
                result.append(f"{i}: {line.rstrip()}")
            return "\n".join(result)
        except Exception as e:
            return f"Error reading file: {e}"
    
    def write_file(self, path: str, content: str) -> str:
        """Write content to a file."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error writing file: {e}"
    
    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        """Edit a file by replacing old_string with new_string."""
        try:
            with open(path, 'r') as f:
                content = f.read()
            
            if old_string not in content:
                return f"Error: old_string not found in {path}"
            
            if content.count(old_string) > 1:
                return f"Error: Found multiple matches for old_string in {path}"
            
            new_content = content.replace(old_string, new_string, 1)
            with open(path, 'w') as f:
                f.write(new_content)
            
            return f"Successfully edited {path}"
        except Exception as e:
            return f"Error editing file: {e}"
    
    def run_bash(self, command: str, timeout: int = 120000) -> str:
        """Execute a bash command."""
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout / 1000,  # Convert ms to seconds
            )
            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]\n{result.stderr}"
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}ms"
        except Exception as e:
            return f"Error running command: {e}"
    
    def glob_files(self, pattern: str, path: Optional[str] = None) -> str:
        """Find files matching a glob pattern."""
        try:
            if path:
                full_pattern = os.path.join(path, pattern)
            else:
                full_pattern = pattern
            
            matches = glob_module.glob(full_pattern, recursive=True)
            if not matches:
                return "(no matches found)"
            return "\n".join(matches)
        except Exception as e:
            return f"Error in glob: {e}"
    
    def grep_search(self, pattern: str, path: Optional[str] = None, include: Optional[str] = None) -> str:
        """Search file contents using regex."""
        try:
            results = []
            search_path = path or "."
            
            for root, dirs, files in os.walk(search_path):
                # Skip hidden directories and common ignore patterns
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv']]
                
                for file in files:
                    if include and not glob_module.fnmatch.fnmatch(file, include):
                        continue
                    
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, 'r', errors='replace') as f:
                            for i, line in enumerate(f, 1):
                                if re.search(pattern, line):
                                    results.append(f"{filepath}:{i}: {line.rstrip()}")
                    except:
                        continue
            
            return "\n".join(results[:100]) if results else "(no matches found)"
        except Exception as e:
            return f"Error in grep: {e}"

    def execute(self, tool_name: str, arguments: Dict) -> str:
        """Execute a tool by name and return the result."""
        if tool_name == "read":
            return self.read_file(
                arguments.get("filePath", ""),
                arguments.get("offset", 0),
                arguments.get("limit", 2000)
            )
        elif tool_name == "write":
            return self.write_file(
                arguments.get("filePath", ""),
                arguments.get("content", "")
            )
        elif tool_name == "edit":
            return self.edit_file(
                arguments.get("filePath", ""),
                arguments.get("oldString", ""),
                arguments.get("newString", "")
            )
        elif tool_name == "bash":
            return self.run_bash(
                arguments.get("command", ""),
                arguments.get("timeout", 120000)
            )
        elif tool_name == "glob":
            return self.glob_files(
                arguments.get("pattern", ""),
                arguments.get("path")
            )
        elif tool_name == "grep":
            return self.grep_search(
                arguments.get("pattern", ""),
                arguments.get("path"),
                arguments.get("include")
            )
        else:
            return f"Unknown tool: {tool_name}"


class AgentSwarm:
    """Analyzes user messages and decides which tools to call."""
    
    def __init__(self):
        self.executor = ToolExecutor()
        self.tool_calls = []
    
    def analyze(self, message: str, tools: List[Dict]) -> Optional[Tuple[str, Dict]]:
        """
        Analyze message and decide if a tool should be called.
        Returns (tool_name, arguments) or None.
        """
        message_lower = message.lower().strip()

        # Get available tool names
        available_tools = {t.get("function", {}).get("name", ""): t for t in tools}

        # Pattern: Run command (check FIRST — "run ls /tmp" should not match "read")
        if any(x in message_lower for x in ["run ", "execute ", "command ", "bash ", "shell ", "`"]):
            cmd = self._extract_command(message)
            if cmd:
                return ("bash", {"command": cmd})

        # Pattern: Explicit bash command with $
        if "$ " in message or message.startswith("$"):
            cmd = self._extract_command(message)
            if cmd:
                return ("bash", {"command": cmd})

        # Pattern: Read file
        if "read" in message_lower and ("file" in message_lower or "/" in message):
            path = self._extract_path(message, ["read", "file", "show", "cat"])
            if path:
                return ("read", {"filePath": path})

        # Pattern: cat command
        if message_lower.startswith("cat ") or message_lower.startswith("cat\t"):
            path = self._extract_path(message, ["cat"])
            if path:
                return ("read", {"filePath": path})

        # Pattern: List directory
        if any(x in message_lower for x in ["list", "ls ", "ls\t", "dir ", "directory", "what's in"]):
            path = self._extract_path(message, ["list", "ls", "dir", "directory", "in"])
            if path:
                return ("read", {"filePath": path})

        # Pattern: Write/Create file
        if any(x in message_lower for x in ["write ", "create ", "save "]):
            path = self._extract_path(message, ["write", "create", "save", "file", "to"])
            if path:
                content = self._extract_content(message)
                if content:
                    return ("write", {"filePath": path, "content": content})

        # Pattern: Edit file
        if "edit" in message_lower or "replace" in message_lower or "change" in message_lower:
            path = self._extract_path(message, ["edit", "replace", "change", "file", "in"])
            if path:
                old_str, new_str = self._extract_edit_strings(message)
                if old_str and new_str:
                    return ("edit", {"filePath": path, "oldString": old_str, "newString": new_str})

        # Pattern: Find files
        if any(x in message_lower for x in ["find files", "search files", "glob", "pattern"]):
            pattern = self._extract_pattern(message)
            if pattern:
                return ("glob", {"pattern": pattern})

        # Pattern: Search in files (grep)
        if any(x in message_lower for x in ["grep", "search in", "find text", "search for"]):
            pattern = self._extract_grep_pattern(message)
            if pattern:
                return ("grep", {"pattern": pattern})

        return None
    
    def _extract_path(self, message: str, keywords: List[str]) -> Optional[str]:
        """Extract a file path from the message."""
        skip_words = {"file", "path", "named", "called", "the", "a", "an", "show", "of", "in", "to", "from"}

        # Try quoted path first
        quoted = re.findall(r'["\']([^"\']+)["\']', message)
        if quoted:
            return quoted[0]

        # Try to find any path token (words containing /)
        # Match: src/main.py, /tmp, /Users/foo/bar, ./env
        path_tokens = re.findall(r'(\.{0,2}/\S+|[^ \t]*\/[^ \t]*)', message)
        for token in path_tokens:
            clean = token.rstrip('.,;:!?\'"')
            if clean:
                return clean

        # Fallback: last word that looks like a path (starts with . ~ /)
        words = message.split()
        for word in reversed(words):
            clean = word.rstrip('.,;:!?\'"')
            if clean and (clean.startswith('/') or clean.startswith('~') or clean.startswith('.')):
                return clean

        # Try path after keyword — skip non-path words
        for keyword in keywords:
            if keyword.lower() in skip_words:
                continue
            pattern = rf'{keyword}\s+(\S+)'
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                path = match.group(1).rstrip('.,;:!?\'"')
                if path and path.lower() not in skip_words:
                    return path

        return None
    
    def _extract_content(self, message: str) -> Optional[str]:
        """Extract content to write from the message."""
        # Try quoted content
        quoted = re.findall(r'["\']([^"\']+)["\']', message)
        if len(quoted) > 1:
            return quoted[1]  # Second quoted string is likely content
        
        # Try content after "with" or "containing"
        for keyword in ["with", "containing", "content", "text"]:
            pattern = rf'{keyword}\s+["\']([^"\']+)["\']'
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _extract_edit_strings(self, message: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract old and new strings for editing."""
        # Try "replace X with Y" pattern
        pattern = r'replace\s+["\']([^"\']+)["\']\s+with\s+["\']([^"\']+)["\']'
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        
        # Try "change X to Y" pattern
        pattern = r'change\s+["\']([^"\']+)["\']\s+to\s+["\']([^"\']+)["\']'
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        
        return None, None
    
    def _extract_command(self, message: str) -> Optional[str]:
        """Extract a command to run."""
        # Try backtick-wrapped command
        backtick = re.search(r'`([^`]+)`', message)
        if backtick:
            return backtick.group(1)
        
        # Try command after keywords
        for keyword in ["run", "execute", "command", "bash", "shell"]:
            pattern = rf'{keyword}\s+(.+)'
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                cmd = match.group(1).strip()
                # Remove quotes if present
                cmd = cmd.strip('"\'')
                return cmd
        
        # Try after $
        dollar = re.search(r'\$\s*(.+)', message)
        if dollar:
            return dollar.group(1).strip()
        
        return None
    
    def _extract_pattern(self, message: str) -> Optional[str]:
        """Extract a glob pattern."""
        # Try quoted pattern
        quoted = re.findall(r'["\']([^"\']+)["\']', message)
        if quoted:
            return quoted[0]
        
        # Try pattern after keywords
        for keyword in ["find", "search", "glob", "pattern"]:
            pattern = rf'{keyword}\s+([^\s,]+)'
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def _extract_grep_pattern(self, message: str) -> Optional[str]:
        """Extract a grep pattern."""
        # Try quoted pattern
        quoted = re.findall(r'["\']([^"\']+)["\']', message)
        if quoted:
            return quoted[0]
        
        # Try pattern after keywords
        for keyword in ["grep", "search for", "find text", "search in"]:
            pattern = rf'{keyword}\s+([^\s,]+)'
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    def execute(self, tool_name: str, arguments: Dict) -> str:
        """Execute a tool and return the result."""
        if tool_name == "read":
            return self.executor.read_file(
                arguments.get("filePath", ""),
                arguments.get("offset", 0),
                arguments.get("limit", 2000)
            )
        elif tool_name == "write":
            return self.executor.write_file(
                arguments.get("filePath", ""),
                arguments.get("content", "")
            )
        elif tool_name == "edit":
            return self.executor.edit_file(
                arguments.get("filePath", ""),
                arguments.get("oldString", ""),
                arguments.get("newString", "")
            )
        elif tool_name == "bash":
            return self.executor.run_bash(
                arguments.get("command", ""),
                arguments.get("timeout", 120000)
            )
        elif tool_name == "glob":
            return self.executor.glob_files(
                arguments.get("pattern", ""),
                arguments.get("path")
            )
        elif tool_name == "grep":
            return self.executor.grep_search(
                arguments.get("pattern", ""),
                arguments.get("path"),
                arguments.get("include")
            )
        else:
            return f"Unknown tool: {tool_name}"
    
    def format_tool_response(self, tool_name: str, tool_call_id: str, result: str) -> Dict:
        """Format a tool result as an OpenAI tool response."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result
        }
    
    def format_assistant_tool_call(self, tool_name: str, tool_call_id: str, arguments: Dict) -> Dict:
        """Format an assistant message with a tool call."""
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments)
                    }
                }
            ]
        }
