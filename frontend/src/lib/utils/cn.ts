import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export type WithElementRef<T, Element extends HTMLElement = HTMLElement> = T & {
	ref?: Element | null;
};

export type WithoutChild<T> = T extends unknown ? Omit<T, "child"> : never;
export type WithoutChildren<T> = T extends unknown ? Omit<T, "children"> : never;
export type WithoutChildrenOrChild<T> = WithoutChildren<WithoutChild<T>>;

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
