import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('frontend shell', () => {
  it('provides a Chinese document language and application mount point', () => {
    const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')

    expect(html).toContain('<html lang="zh-CN">')
    expect(html).toContain('<div id="app"></div>')
  })
})

