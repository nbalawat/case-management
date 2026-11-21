import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/cn'

/** The kit's button. Variants are the only styling a caller chooses; everything else comes from the tokens. */
export const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors cursor-pointer ' +
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:opacity-50 disabled:cursor-not-allowed',
  {
    variants: {
      variant: {
        default: 'bg-surface text-ink border border-line hover:border-line-strong',
        primary: 'bg-brand text-on-brand border border-brand hover:opacity-90',
        ghost: 'bg-transparent text-soft border border-transparent hover:bg-bg-2 hover:text-ink',
      },
      size: { sm: 'h-8 px-2.5', md: 'h-9 px-3.5', icon: 'h-9 w-9 p-0' },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
)

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) =>
  <button ref={ref} className={cn(buttonVariants({ variant, size }), 'min-h-0', className)} {...props} />)
Button.displayName = 'Button'
