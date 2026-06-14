def process_data(input_string):
    """
    A simple example function that takes a string input and returns a formatted output.
    """
    try:
        # Example logic: reverse the string and add some metadata
        result = input_string[::-1]
        return f"Python processed: {result}"
    except Exception as e:
        return f"Error: {str(e)}"

def get_system_info():
    import sys
    return f"Python version: {sys.version}"
