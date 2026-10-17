def pytest_configure(config):
    config.addinivalue_line('markers', 'real_data: exercised here using synthetic XRD instrument reports')
    config.addinivalue_line('markers', 'real_only: requires external real instrument data')
