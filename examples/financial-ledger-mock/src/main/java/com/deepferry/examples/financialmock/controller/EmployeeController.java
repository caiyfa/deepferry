package com.deepferry.examples.financialmock.controller;

import com.deepferry.examples.financialmock.common.ApiResponse;
import com.deepferry.examples.financialmock.entity.Employee;
import com.deepferry.examples.financialmock.repository.EmployeeRepository;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.List;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/v1/employees")
public class EmployeeController {

    private final EmployeeRepository employeeRepository;

    public EmployeeController(EmployeeRepository employeeRepository) {
        this.employeeRepository = employeeRepository;
    }

    @GetMapping
    public ApiResponse<EmployeeDto> listEmployees(@RequestParam(required = false) String department) {
        List<Employee> employees;
        if (department != null && !department.isEmpty()) {
            employees = employeeRepository.findByDepartment(department);
        } else {
            employees = employeeRepository.findAll();
        }
        List<EmployeeDto> dtos = employees.stream()
                .map(EmployeeDto::from)
                .collect(Collectors.toList());
        return ApiResponse.of(dtos);
    }

    public record EmployeeDto(
            Long id,
            String empNo,
            String name,
            String department,
            String position,
            String email,
            LocalDate hireDate) {

        public static EmployeeDto from(Employee e) {
            return new EmployeeDto(
                    e.getId(), e.getEmpNo(), e.getName(),
                    e.getDepartment(), e.getPosition(),
                    e.getEmail(), e.getHireDate());
        }
    }
}
