# Enterprise Data Agent

This is a sample application that demonstrates exploration of structured and unstructured data using and agentic retrieval and NL2SQL. It leverages [Microsoft Agent Framework](https://aka.ms/agent-framework) (MAF) as an agent orchestratory and [assistant-ui](https://github.com/assistant-ui/assistant-ui) for the core user experience. These two pieces work together using the Server Side Events protocol with all thread management delegated to Microsoft Foundry.

## Prerequisites

- Azure OpenAI credentials (for the Microsoft Agent Framework agent)
- Python 3.12+
- uv
- Node.js 20+ 
- Any of the following package managers:
  - pnpm (recommended)
  - npm
  - yarn
  - bun

It is assumed you have administrative permissions to an Azure subscription as well as the ability to register applications in Entra ID.

## Getting Started

### Deploy Azure Infrastructure

Details about infrastructure deployment can be found within the [infra](/infra/) folder.

### Register an App ID in Entra ID

This repo supports user-level authentication to the agent API, which supports enterprise security as well as documenting user feedback. The application can be created using: [create-chat-app.ps1](scripts/create-chat-app.ps1). Be sure to sign-into your Entra ID tenant using `az login` first.

## Run locally

### Install dependencies

Install dependencies using your preferred package manager:

   ```bash
   # Using pnpm (recommended)
   pnpm install

   # Using npm
   npm install

   # Using yarn
   yarn install

   # Using bun
   bun install
   ```

   > **Note:** This automatically sets up the Python environment as well. If you have manual issues, you can run: `npm run install:agent`

### Set environment variables

Using the output from the application enrollment script, set up your agent credentials. The backend automatically uses Azure when the Azure env vars below are present. Create an `.env` file inside the `agent` folder with one of the following configurations:
  
   ```env
   # Microsoft Foundry settings
   AZURE_OPENAI_ENDPOINT=https://[your-resource].services.ai.azure.com/
   AZURE_OPENAI_PROJECT_ENDPOINT=https://[your-resource].services.ai.azure.com/api/projects/[your-project]
   AZURE_OPENAI_CHAT_DEPLOYMENT_NAME=gpt-4o

   # Agent name (optional) used to find or create an agent (default: data-explorer-agent)
   # AZURE_AI_AGENT_NAME=[custom-name-]

   # Entra ID Authentication
   AZURE_AD_CLIENT_ID=[your-app-id]
   AZURE_AD_TENANT_ID=[your-tenant-id]
   ```

> [!IMPORTANT]
> The Entra ID section is optional. When the two environment variables are set, the API will require a valid token issued by the source tenant with the correct target scope. If you don't require user-level authorization to the API, you can delete this section.

Next, create a new `.env.local` file within the `frontend` directory and populate the values. You can use the [.env.example](frontend/.env.example) as a reference.

   ```env
   NEXT_PUBLIC_AZURE_AD_CLIENT_ID=your-client-id-here
   NEXT_PUBLIC_AZURE_AD_TENANT_ID=your-tenant-id-here
   ```

### Start the development server

The following commands can be used to start the enviroment locally:

   ```bash
   # Using pnpm
   pnpm dev

   # Using npm
   npm run dev

   # Using yarn
   yarn dev

   # Using bun
   bun run dev
   ```

   This will start both the UI and the Microsoft Agent Framework server concurrently.

## Telemetry and DevUI

### Running with DevUI

The Microsoft Agent Framework includes a development UI for testing and debugging agents and workflows. To run the application with DevUI instead of the FastAPI server:

```bash
cd api
source .venv/bin/activate
devui ./src/entities
```

DevUI will auto-discover the agents and workflows in the `entities` directory and provide an interactive interface for testing. You can run individual agents (`data_agent`, `chat_agent`) or the full `workflow`.

### Enabling Telemetry

The application supports OpenTelemetry for observability. Add these environment variables to your `api/.env` file:

```env
# Enable OpenTelemetry instrumentation
ENABLE_INSTRUMENTATION=true

# Option 1: Azure Monitor (production)
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...

# Option 2: OTLP exporters (local development with Aspire Dashboard, Jaeger, etc.)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Optional: Enable console output for debugging
ENABLE_CONSOLE_EXPORTERS=true

# Optional: Log prompts and responses (use with caution - contains sensitive data)
ENABLE_SENSITIVE_DATA=true
```

To install the required telemetry packages:

```bash
cd api
uv pip install -e ".[observability]"
```

## Available Scripts

The following scripts can also be run using your preferred package manager:

- `dev` – Starts both UI and agent servers in development mode
- `dev:debug` – Starts development servers with debug logging enabled
- `dev:ui` – Starts only the Next.js UI server
- `dev:agent` – Starts only the Microsoft Agent Framework server
- `build` – Builds the Next.js application for production
- `start` – Starts the production server
- `lint` – Runs ESLint for code linting
- `install:agent` – Installs Python dependencies for the agent
