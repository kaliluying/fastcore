import type { AuthDirective, RippleDirective } from "@/directives";

declare module "vue" {
  export interface GlobalDirectives {
    vAuth: AuthDirective;
    vRipple: RippleDirective;
  }
}
