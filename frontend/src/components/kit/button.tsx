import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/cn'
import { buttonVariants } from './buttonVariants'

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) =>
  <button ref={ref} className={cn(buttonVariants({ variant, size }), 'min-h-0', className)} {...props} />)
Button.displayName = 'Button'
