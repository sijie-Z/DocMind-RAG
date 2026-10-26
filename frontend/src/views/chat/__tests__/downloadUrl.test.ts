import { describe, expect, it } from 'vitest'
import { parseTrustedDownloadUrl } from '../utils/downloadUrl'

const HOST_JWT = 'eyJhbGciOiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c'
const PATH_JWT = 'eyJhbGciOiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c'

function nestedEncoding(value: string, rounds: number): string {
  let encoded = value
  for (let index = 0; index < rounds; index += 1) encoded = encodeURIComponent(encoded)
  return encoded
}

describe('parseTrustedDownloadUrl canonicalization', () => {
  it.each([
    'https://minio.test/42/scope.pdf?X-Amz-Signature=fake',
    'https://minio.test/42/%E6%96%87%E6%A1%A3.pdf?X-Amz-Signature=fake',
    'https://www.example.com/object/file.pdf?X-Amz-Signature=fake',
    'https://minio.example.com./42/scope.pdf?X-Amz-Signature=fake',
    'https://minio.test/100%25/scope.pdf?X-Amz-Signature=fake',
    'https://minio.test/api/../42/scope.pdf?X-Amz-Signature=fake',
    'https://minio.test/42/scope.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA%2F20261027&X-Amz-Date=20261027T000000Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Security-Token=FwoGZXIvYXdzEBQaDExampleToken1234567890%2B%2F%3D%3D&X-Amz-Signature=0123456789abcdef',
  ])('preserves the exact original URL for valid candidates: %s', (url) => {
    expect(parseTrustedDownloadUrl(url)).toBe(url)
  })

  it.each([
    'https://backend.test/%61%70%69/v1/documents/doc-7/download',
    'https://backend.test//api/v1/documents/doc-7/download',
    'https://backend.test/api%2Fv1%2Fdocuments%2Fdoc-7%2Fdownload',
    'https://backend.test/%2561%2570%2569/v1/documents/doc-7/download',
    'https://backend.test/\\api\\v1\\documents\\doc-7\\download',
    'https://backend.test/safe/../api/v1/documents/doc-7/download',
    'https://backend.test/safe/%2E%2E/api/v1/documents/doc-7/download',
    `https://minio.test/42/${PATH_JWT.replaceAll('.', '%2E')}`,
    `https://${HOST_JWT}/42/scope.pdf`,
    `https://${HOST_JWT}./42/scope.pdf`,
    'https://minio.test/42/scope.pdf?%61ccess_token=token-A',
    'https://minio.test/42/scope.pdf?%2561ccess_token=token-A',
    'https://minio.test/42/scope.pdf?AUTHORIZATION=Bearer',
    'https://minio.test/42/scope.pdf#%61ccess_token=token-A',
    'https://minio.test/42/scope.pdf?code=aaa%2Ebbb%2Eccc',
    'https://minio.test/42/scope.pdf%0A?X-Amz-Signature=fake',
    'https://minio.test/42/scope%ZZ.pdf',
    'https://minio.test/42/scope%.pdf',
    'https://minio.test/42/%E0%A4%A',
    `https://backend.test/${nestedEncoding('%61', 6)}/v1/documents/doc-7/download`,
  ])('rejects encoded, malformed, protected, or credential payloads: %s', (url) => {
    expect(parseTrustedDownloadUrl(url)).toBeNull()
  })

  it('fails malformed decoding closed without throwing', () => {
    expect(() => parseTrustedDownloadUrl('https://minio.test/%E0%A4%A')).not.toThrow()
    expect(parseTrustedDownloadUrl('https://minio.test/%E0%A4%A')).toBeNull()
  })
})
