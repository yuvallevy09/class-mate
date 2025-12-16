import { client } from "./client";




export const Core = client.integrations.Core;

export const InvokeLLM = client.integrations.Core.InvokeLLM;

// Note: Uploads are implemented via S3 presigned URLs (`/api/v1/uploads/presign`) + direct PUT,
// so this helper is not used by the current UI. Keep for future integrations if needed.
export const UploadFile = client.integrations.Core.UploadFile;






