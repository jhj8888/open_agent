## ADDED Requirements

### Requirement: Clearly labeled interactive preview
The preview SHALL present Agent management, Skills, tasks, publication examples, monitoring, tenants and chat interaction while continuously identifying its data and execution as simulated.

#### Scenario: Review a simulated task
- **WHEN** a user submits a chat message in the preview
- **THEN** an in-memory task transitions through simulated states and its response identifies itself as non-model output
- **AND** no model request, uploaded-code execution or backend task is created

#### Scenario: Cancel a simulated task
- **WHEN** a user cancels a pending or running simulated task
- **THEN** its state remains cancelled after completion timers would otherwise fire

### Requirement: Standalone static deployment
The preview SHALL use a standalone static service without accepting credentials, uploading files or changing existing application services.

#### Scenario: Serve an approved build
- **WHEN** a matching SHA-256 archive is installed on the authorized demo server
- **THEN** a non-root service serves only that build's public files and health endpoint

#### Scenario: Reject invalid deployment or asset access
- **WHEN** an archive digest differs, an unrelated service occupies the port, or a request targets a non-public file
- **THEN** the operation fails without replacing the unrelated service or exposing a filesystem path
