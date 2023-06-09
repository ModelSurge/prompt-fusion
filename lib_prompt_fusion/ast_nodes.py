from lib_prompt_fusion.catmull import compute_catmull
from lib_prompt_fusion.bezier import compute_on_curve_with_points as compute_bezier
from lib_prompt_fusion.linear import compute_linear
from lib_prompt_fusion.t_scaler import scale_t
from lib_prompt_fusion.interpolation_tensor import InterpolationTensorBuilder


class ListExpression:
    def __init__(self, expressions):
        self.__expressions = expressions

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        if not self.__expressions:
            return

        def expr_extend_tensor(expr):
            expr.extend_tensor(tensor_builder, steps_range, total_steps, context)

        expr_extend_tensor(self.__expressions[0])
        for expression in self.__expressions[1:]:
            tensor_builder.append(' ')
            expr_extend_tensor(expression)


class InterpolationExpression:
    def __init__(self, expressions, steps, function_name=None):
        assert len(expressions) >= 2
        assert len(steps) == len(expressions), 'the number of steps must be the same as the number of expressions'
        self.__expressions = expressions
        self.__steps = steps
        self.__function_name = function_name if function_name is not None else 'linear'

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        def tensor_updater(expr):
            return lambda t: expr.extend_tensor(t, steps_range, total_steps, context)

        tensor_builder.extrude(
            [tensor_updater(expr) for expr in self.__expressions],
            self.get_interpolation_function(steps_range, total_steps, context))

    def get_interpolation_function(self, steps_range, total_steps, context):
        steps = list(self.__steps)
        if steps[0] is None:
            steps[0] = LiftExpression(str(steps_range[0] - 1))
        if steps[-1] is None:
            steps[-1] = LiftExpression(str(steps_range[1] - 1))

        for i, step in enumerate(steps):
            step = _eval_float(step, steps_range, total_steps, context)
            if 0 < step < 1:
                step *= total_steps
            else:
                step += 1

            steps[i] = int(step)

        interpolation_function = {
            'catmull': compute_catmull,
            'linear': compute_linear,
            'bezier': compute_bezier,
        }[self.__function_name]

        def steps_scale_t(t, conditionings):
            scaled_t = (t * total_steps - steps[0]) / max(1, steps[-1] - steps[0])
            scaled_t = scale_t(scaled_t, steps)
            return interpolation_function(scaled_t, conditionings)

        return steps_scale_t


class EditingExpression:
    def __init__(self, expressions, step):
        assert 1 <= len(expressions) <= 2
        self.__expressions = expressions
        self.__step = step

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        step = _eval_float(self.__step, steps_range, total_steps, context)
        if 0 < step < 1:
            step *= total_steps
        else:
            step += 1

        step = int(step)

        tensor_builder.append('[')
        for expr_i, expr in enumerate(self.__expressions):
            expr_steps_range = (steps_range[0], step) if len(self.__expressions) > 1 and expr_i == 0 else (step, steps_range[1])
            expr.extend_tensor(tensor_builder, expr_steps_range, total_steps, context)
            tensor_builder.append(':')

        tensor_builder.append(f'{step - 1}]')


class WeightedExpression:
    def __init__(self, nested, weight=None, positive=True):
        self.__nested = nested
        if not positive:
            assert weight is None

        self.__weight = weight
        self.__positive = positive

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        open_bracket, close_bracket = ('(', ')') if self.__positive else ('[', ']')
        tensor_builder.append(open_bracket)
        self.__nested.extend_tensor(tensor_builder, steps_range, total_steps, context)

        if self.__weight is not None:
            tensor_builder.append(':')
            self.__weight.extend_tensor(tensor_builder, steps_range, total_steps, context)

        tensor_builder.append(close_bracket)


class WeightInterpolationExpression:
    def __init__(self, nested, weight_begin, weight_end):
        self.__nested = nested
        self.__weight_begin = weight_begin if weight_begin is not None else LiftExpression(str(1.))
        self.__weight_end = weight_end if weight_end is not None else LiftExpression(str(1.))

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        steps_range_size = steps_range[1] - steps_range[0]

        weight_begin = _eval_float(self.__weight_begin, steps_range, total_steps, context)
        weight_end = _eval_float(self.__weight_end, steps_range, total_steps, context)

        for i in range(steps_range_size):
            step = i + steps_range[0]

            weight = weight_begin + (weight_end - weight_begin) * (i / max(steps_range_size - 1, 1))
            weight_step_expr = WeightedExpression(self.__nested, LiftExpression(str(weight)))
            if step > steps_range[0]:
                weight_step_expr = EditingExpression([weight_step_expr], LiftExpression(str(step - 1)))
            if step + 1 < steps_range[1]:
                weight_step_expr = EditingExpression([weight_step_expr, ListExpression([])], LiftExpression(str(step)))

            weight_step_expr.extend_tensor(tensor_builder, steps_range, total_steps, context)


class DeclarationExpression:
    def __init__(self, symbol, parameters, nested, expression):
        self.__symbol = symbol
        self.__nested = nested
        self.__expression = expression
        self.__parameters = parameters

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        updated_context = dict(context)
        updated_context[self.__symbol] = (self.__nested, self.__parameters)
        self.__expression.extend_tensor(tensor_builder, steps_range, total_steps, updated_context)


class SubstitutionExpression:
    def __init__(self, symbol, arguments):
        self.__symbol = symbol
        self.__arguments = arguments

    def extend_tensor(self, tensor_builder, steps_range, total_steps, context):
        updated_context = dict(context)
        nested, parameters = context[self.__symbol]
        for argument, parameter in zip(self.__arguments, parameters):
            updated_context[parameter] = argument, []
        nested.extend_tensor(tensor_builder, steps_range, total_steps, updated_context)


class LiftExpression:
    def __init__(self, value):
        self.__value = value

    def extend_tensor(self, tensor_builder, *_args, **_kwargs):
        tensor_builder.append(self.__value)


def _eval_float(expression, steps_range, total_steps, context):
    mock_database = ['']
    expression.extend_tensor(InterpolationTensorBuilder(prompt_database=mock_database), steps_range, total_steps, context)
    return float(mock_database[0])
