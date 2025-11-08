// script.js
// Calculator logic – vanilla JavaScript

// ---------------------------------------------------------------------------
// DOM element selection
// ---------------------------------------------------------------------------
const display = document.getElementById('display');
const buttons = document.querySelectorAll('.calc-btn');

// ---------------------------------------------------------------------------
// State variables
// ---------------------------------------------------------------------------
let currentInput = '';
let previousValue = null;
let operator = null;
let shouldResetDisplay = false;

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------
/**
 * Append a digit or decimal point to the current input.
 * @param {string} num - Single digit or '.'
 */
function appendNumber(num) {
    if (shouldResetDisplay) {
        currentInput = '';
        shouldResetDisplay = false;
    }

    if (num === '.') {
        if (currentInput.includes('.')) return; // prevent multiple decimals
        if (currentInput === '') currentInput = '0';
    }
    currentInput += num;
    updateDisplay();
}

/**
 * Choose an operator. If a previous value and operator exist, compute first.
 * @param {string} op - '+', '-', '*', '/'
 */
function chooseOperator(op) {
    if (currentInput === '') return; // no number to operate on

    if (previousValue !== null && operator !== null) {
        compute();
    }
    previousValue = parseFloat(currentInput);
    operator = op;
    shouldResetDisplay = true;
}

/**
 * Perform the calculation based on the current operator.
 * Handles division by zero.
 */
function compute() {
    if (operator === null || previousValue === null || currentInput === '') return;

    const currentValue = parseFloat(currentInput);
    let result;
    switch (operator) {
        case '+': result = previousValue + currentValue; break;
        case '-': result = previousValue - currentValue; break;
        case '*': result = previousValue * currentValue; break;
        case '/':
            if (currentValue === 0) {
                result = 'Error';
            } else {
                result = previousValue / currentValue;
            }
            break;
        default: return;
    }

    // Update display and state
    if (result === 'Error') {
        display.value = result;
        resetCalculator();
        return;
    }
    display.value = result;
    currentInput = result.toString();
    previousValue = null;
    operator = null;
    shouldResetDisplay = true;
}

/**
 * Clear the current entry (reset current input).
 */
function clearEntry() {
    currentInput = '';
    updateDisplay();
}

/**
 * Reset the entire calculator state.
 */
function resetCalculator() {
    currentInput = '';
    previousValue = null;
    operator = null;
    shouldResetDisplay = false;
    updateDisplay();
}

/**
 * Update the display element based on currentInput.
 */
function updateDisplay() {
    display.value = currentInput || '0';
}

/**
 * Handle keyboard input.
 * @param {KeyboardEvent} event
 */
function handleKeyboard(event) {
    const key = event.key;
    if (/^[0-9]$/.test(key)) {
        appendNumber(key);
    } else if (key === '.') {
        appendNumber('.');
    } else if (['+', '-', '*', '/'].includes(key)) {
        chooseOperator(key);
    } else if (key === 'Enter' || key === '=') {
        compute();
    } else if (key === 'Backspace') {
        clearEntry();
    } else if (key === 'Escape') {
        resetCalculator();
    }
}

/**
 * Handle button press events based on data-value attribute.
 * @param {string} value
 */
function handleButtonPress(value) {
    if (/^[0-9]$/.test(value) || value === '.') {
        appendNumber(value);
    } else if (['+', '-', '*', '/'].includes(value)) {
        chooseOperator(value);
    } else if (value === '=' || value === 'Enter') {
        compute();
    } else if (value === 'C') {
        clearEntry();
    } else if (value === '←' || value === 'Backspace') {
        resetCalculator();
    }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
buttons.forEach(button => {
    button.addEventListener('click', () => {
        const value = button.dataset.value;
        handleButtonPress(value);
        // Optional visual feedback
        button.classList.add('active');
        setTimeout(() => button.classList.remove('active'), 100);
    });
});

document.addEventListener('keydown', handleKeyboard);

// Initialize display
updateDisplay();

// No exports – script runs in the browser context
