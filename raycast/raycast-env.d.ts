/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Repository Path - Absolute path to the financial-assistant repository containing finance_cli.py. */
  "repoPath": string
}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `quick-expense` command */
  export type QuickExpense = ExtensionPreferences & {}
  /** Preferences accessible in the `refresh-templates` command */
  export type RefreshTemplates = ExtensionPreferences & {}
}

declare namespace Arguments {
  /** Arguments passed to the `quick-expense` command */
  export type QuickExpense = {}
  /** Arguments passed to the `refresh-templates` command */
  export type RefreshTemplates = {}
}

