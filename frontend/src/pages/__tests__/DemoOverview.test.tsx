import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DemoOverview } from '../DemoOverview'

describe('DemoOverview', () => {
  it('labels the sample fleet read-only and shows seeded data', () => {
    render(<DemoOverview />)

    expect(screen.getByRole('status')).toHaveTextContent('DEMO DATA · READ ONLY')
    expect(screen.getByText('linux-builder-01')).toBeInTheDocument()
    expect(screen.getByText('Frontend tests · web-console')).toBeInTheDocument()
    expect(screen.getByText(/does not connect to GitHub or change any external services/)).toBeInTheDocument()
  })
})
