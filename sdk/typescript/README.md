# @caseforge/client (TypeScript)

The same client as the Python one, for Node 20+, Deno, Bun and browsers, with no runtime dependencies.

```ts
import { CaseForge, verifyWebhook } from '@caseforge/client'

const cf = new CaseForge({ baseUrl: 'http://localhost:8000', clientId: 'cfc_...', clientSecret: 'cfs_...', scopes: ['cases:read'] })
for await (const c of cf.paginate('/cases', { status: 'open', priority: 'high' })) console.log(c)
```

`npm run types` writes `src/schema.d.ts` from a running deployment's OpenAPI document (openapi-typescript), for
fully typed request and response bodies.
