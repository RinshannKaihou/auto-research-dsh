function schema(spec) {
  const value = { ...spec };
  delete value.required;
  if (spec.type === 'object') {
    const properties = Object.fromEntries(
      Object.entries(spec.properties ?? {}).map(([name, child]) => [name, schema(child)]),
    );
    const required = Object.entries(spec.properties ?? {})
      .filter(([, child]) => child.required)
      .map(([name]) => name);
    value.properties = properties;
    if (required.length) value.required = required;
  }
  if (spec.type === 'array' && spec.items) value.items = schema(spec.items);
  return value;
}

function validate(value, spec, path) {
  if (value === undefined) {
    if (spec.required) throw new Error(`${path} is required`);
    return;
  }
  if (spec.type === 'string' && typeof value !== 'string') throw new Error(`${path} must be text`);
  if (spec.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) {
    throw new Error(`${path} must be a finite number`);
  }
  if (spec.type === 'boolean' && typeof value !== 'boolean') {
    throw new Error(`${path} must be true or false`);
  }
  if (spec.type === 'array') {
    if (!Array.isArray(value)) throw new Error(`${path} must be an array`);
    for (let index = 0; index < value.length; index += 1) {
      validate(value[index], spec.items ?? {}, `${path}[${index}]`);
    }
  }
  if (spec.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`${path} must be an object`);
    }
    for (const [name, child] of Object.entries(spec.properties ?? {})) {
      validate(value[name], child, `${path}.${name}`);
    }
  }
  if (spec.enum && !spec.enum.includes(value)) throw new Error(`${path} is not an allowed value`);
}

export function defineResearchTool(options) {
  const parameterSpec = options.parameters ?? {};
  const parameters = {
    type: 'object',
    additionalProperties: false,
    properties: Object.fromEntries(
      Object.entries(parameterSpec).map(([name, value]) => [name, schema(value)]),
    ),
  };
  const required = Object.entries(parameterSpec)
    .filter(([, value]) => value.required)
    .map(([name]) => name);
  if (required.length) parameters.required = required;
  return {
    ...options,
    parameters,
    async execute(args, exec) {
      if (!args || typeof args !== 'object' || Array.isArray(args)) {
        throw new Error('tool arguments must be an object');
      }
      for (const [name, spec] of Object.entries(parameterSpec)) {
        validate(args[name], spec, name);
      }
      return options.execute(args, exec);
    },
  };
}
